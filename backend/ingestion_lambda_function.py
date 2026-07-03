import json
import boto3
import os
import re
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────
CHUNKS_TABLE    = os.environ.get('CHUNKS_TABLE_NAME', 'legal-aid-chunks')
DOCUMENTS_TABLE = os.environ.get('DOCUMENTS_TABLE', 'legal-aid-documents')
BUCKET_NAME     = os.environ.get('S3_BUCKET_NAME', '')
REGION          = os.environ.get('REGION', 'us-east-1')
CHUNK_SIZE      = int(os.environ.get('CHUNK_SIZE', '300'))
CHUNK_OVERLAP   = int(os.environ.get('CHUNK_OVERLAP', '50'))
MODEL_ID        = os.environ.get('BEDROCK_MODEL_ID',
                                  'us.amazon.nova-lite-v1:0')

# ── AWS clients ────────────────────────────────────────────────
s3        = boto3.client('s3', region_name=REGION)
dynamodb  = boto3.resource('dynamodb', region_name=REGION)
bedrock   = boto3.client('bedrock-runtime', region_name=REGION)
table     = dynamodb.Table(CHUNKS_TABLE)
doc_table = dynamodb.Table(DOCUMENTS_TABLE)

# ── Category map ───────────────────────────────────────────────
CATEGORY_MAP = {
    'tenant-rights':       'tenant-rights',
    'employment-rights':   'employment-rights',
    'government-benefits': 'government-benefits',
    'immigration':         'immigration',
    'general':             'general'
}

CATEGORY_KEYWORDS = {
    'tenant-rights': [
        'eviction','tenant','landlord','rent','lease','deposit',
        'notice','property','agreement','court','remove','vacate',
        'rental','premises','termination','repair','maintenance'
    ],
    'employment-rights': [
        'employee','employer','salary','wage','termination','contract',
        'dismissal','notice','compensation','leave','maternity',
        'discrimination','harassment','overtime','provident','gratuity'
    ],
    'government-benefits': [
        'pension','disability','welfare','scheme','benefit','allowance',
        'subsidy','ration','card','eligibility','application','income',
        'poverty','assistance','grant','insurance'
    ],
    'immigration': [
        'visa','passport','citizenship','asylum','refugee','permit',
        'immigration','deportation','nationality','resident','foreign',
        'border','documentation','status','application','renewal'
    ],
    'general': [
        'legal','rights','law','court','claim','dispute','contract',
        'agreement','obligation','liability','penalty','compensation'
    ]
}


# ══════════════════════════════════════════════════════════════
#  MAIN HANDLER
# ══════════════════════════════════════════════════════════════

def lambda_handler(event, context):
    print(f"Ingestion Lambda started. Event: {json.dumps(event)}")

    results = {
        'files_processed': 0,
        'chunks_created':  0,
        'errors':          []
    }

    # Determine trigger mode
    if 'Records' in event and \
       event['Records'][0].get('eventSource') == 'aws:s3':
        files_to_process = []
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key    = record['s3']['object']['key']
            files_to_process.append((bucket, key))
        print(f"S3 trigger: {len(files_to_process)} file(s)")
    else:
        files_to_process = list_all_s3_files(BUCKET_NAME)
        print(f"Manual: {len(files_to_process)} files")

    for bucket, key in files_to_process:
        try:
            file_result = process_file(bucket, key)
            results['files_processed'] += 1
            results['chunks_created']  += file_result['chunks_created']
            print(f"✓ {key} → {file_result['chunks_created']} chunks")
        except Exception as e:
            error_msg = f"Error processing {key}: {str(e)}"
            results['errors'].append(error_msg)
            print(f"✗ {error_msg}")

    print(f"Complete: {json.dumps(results)}")
    return {'statusCode': 200, 'body': json.dumps(results)}


# ══════════════════════════════════════════════════════════════
#  FILE LISTING
# ══════════════════════════════════════════════════════════════

def list_all_s3_files(bucket):
    files = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('/'):
                continue
            if key.endswith('.txt') or key.endswith('.pdf'):
                files.append((bucket, key))
    return files


# ══════════════════════════════════════════════════════════════
#  FILE PROCESSING
# ══════════════════════════════════════════════════════════════

def process_file(bucket, key):
    category = get_category_from_key(key)
    if not category:
        print(f"Skipping {key} — no matching category")
        return {'chunks_created': 0}

    raw_content = read_s3_file(bucket, key)
    if not raw_content:
        print(f"Skipping {key} — empty file")
        return {'chunks_created': 0}

    source_file = key.split('/')[-1]
    document_id = f"{category}#{source_file}"

    # Delete existing chunks for clean re-ingestion
    delete_existing_chunks(category, source_file, document_id)

    # ── Extract content by type ────────────────────────────────
    if key.endswith('.pdf'):
        extraction = extract_pdf_multimodal(raw_content)
    else:
        extraction = {
            'text_chunks': split_into_chunks(
                raw_content.decode('utf-8', errors='ignore'),
                CHUNK_SIZE, CHUNK_OVERLAP
            ),
            'tables':  [],
            'images':  []
        }

    chunks_created = 0

    # ── Write text chunks ──────────────────────────────────────
    with table.batch_writer() as batch:
        for i, chunk_text in enumerate(extraction['text_chunks']):
            chunk_id = f"{document_id}-text-{i:04d}"
            keywords = extract_keywords(chunk_text, category)
            batch.put_item(Item={
                'category':    category,
                'chunkId':     chunk_id,
                'documentId':  document_id,
                'content':     chunk_text,
                'sourceFile':  source_file,
                'keywords':    keywords,
                'chunkIndex':  str(i),
                'chunk_type':  'text',
                'createdAt':   datetime.now(timezone.utc).isoformat(),
                'wordCount':   len(chunk_text.split())
            })
            chunks_created += 1

    # ── Write table chunks (with Bedrock summary) ──────────────
    for i, table_text in enumerate(extraction['tables']):
        summary  = summarize_with_bedrock(table_text, 'table')
        chunk_id = f"{document_id}-table-{i:04d}"
        keywords = extract_keywords(summary, category)
        table.put_item(Item={
            'category':   category,
            'chunkId':    chunk_id,
            'documentId': document_id,
            'content':    summary,
            'summary':    summary,
            'sourceFile': source_file,
            'keywords':   keywords,
            'chunkIndex': str(10000 + i),
            'chunk_type': 'table',
            'raw_data':   table_text[:2000],
            'createdAt':  datetime.now(timezone.utc).isoformat(),
            'wordCount':  len(summary.split())
        })
        chunks_created += 1
        print(f"Table chunk {i} summarized: {summary[:80]}...")

    # ── Write image chunks (with Bedrock description) ──────────
    for i, image_desc in enumerate(extraction['images']):
        if not image_desc.strip():
            continue
        summary  = summarize_with_bedrock(image_desc, 'image')
        chunk_id = f"{document_id}-image-{i:04d}"
        keywords = extract_keywords(summary, category)
        table.put_item(Item={
            'category':   category,
            'chunkId':    chunk_id,
            'documentId': document_id,
            'content':    summary,
            'summary':    summary,
            'sourceFile': source_file,
            'keywords':   keywords,
            'chunkIndex': str(20000 + i),
            'chunk_type': 'image',
            'createdAt':  datetime.now(timezone.utc).isoformat(),
            'wordCount':  len(summary.split())
        })
        chunks_created += 1
        print(f"Image chunk {i} described: {summary[:80]}...")

    # ── Update document metadata ───────────────────────────────
    update_document_status(document_id, source_file, category,
                           key, chunks_created)

    return {'chunks_created': chunks_created}


# ══════════════════════════════════════════════════════════════
#  PDF MULTIMODAL EXTRACTION
# ══════════════════════════════════════════════════════════════

def extract_pdf_multimodal(pdf_bytes):
    """
    Extract text, tables, and image descriptions from a PDF.
    Uses PyMuPDF (fitz) which must be in a Lambda Layer.
    """
    result = {'text_chunks': [], 'tables': [], 'images': []}

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text_pages = []

        for page_num in range(len(doc)):
            page = doc[page_num]

            # ── Extract plain text ─────────────────────────────
            text = page.get_text("text")
            lines = [l.strip() for l in text.split('\n')]
            lines = [l for l in lines if len(l) > 3]
            page_text = ' '.join(lines)
            if page_text.strip():
                full_text_pages.append(page_text)

            # ── Extract tables (PyMuPDF 1.23+) ────────────────
            try:
                tabs = page.find_tables()
                for tab in tabs.tables:
                    rows = tab.extract()
                    if not rows:
                        continue
                    table_lines = []
                    for row in rows:
                        cells = [str(cell).strip()
                                 if cell else ''
                                 for cell in row]
                        table_lines.append(' | '.join(cells))
                    table_text = '\n'.join(table_lines)
                    if len(table_text.strip()) > 20:
                        result['tables'].append(table_text)
                        print(f"Page {page_num+1}: table extracted "
                              f"({len(rows)} rows)")
            except Exception as te:
                print(f"Table extraction page {page_num+1}: {te}")

            # ── Extract embedded images ────────────────────────
            try:
                image_list = page.get_images(full=True)
                for img_idx, img_info in enumerate(image_list):
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)
                    width  = base_image.get('width', 0)
                    height = base_image.get('height', 0)
                    # Skip tiny images (icons, decorations)
                    if width < 100 or height < 100:
                        continue
                    img_desc = (f"Image on page {page_num+1}: "
                                f"{width}x{height}px. "
                                f"This appears to be a figure or "
                                f"diagram in the document.")
                    result['images'].append(img_desc)
                    print(f"Page {page_num+1}: image "
                          f"{width}x{height} extracted")
            except Exception as ie:
                print(f"Image extraction page {page_num+1}: {ie}")

        doc.close()

        # ── Chunk the full text ────────────────────────────────
        full_text = '\n\n'.join(full_text_pages)
        if full_text.strip():
            result['text_chunks'] = split_into_chunks(
                full_text, CHUNK_SIZE, CHUNK_OVERLAP
            )
        print(f"Extracted: {len(result['text_chunks'])} text chunks, "
              f"{len(result['tables'])} tables, "
              f"{len(result['images'])} images")

    except ImportError:
        print("PyMuPDF not available — fallback to basic extraction")
        text = extract_text_basic_fallback(pdf_bytes)
        result['text_chunks'] = split_into_chunks(
            text, CHUNK_SIZE, CHUNK_OVERLAP
        )
    except Exception as e:
        print(f"PDF extraction error: {e}")
        text = extract_text_basic_fallback(pdf_bytes)
        result['text_chunks'] = split_into_chunks(
            text, CHUNK_SIZE, CHUNK_OVERLAP
        )

    return result


def extract_text_basic_fallback(pdf_bytes):
    text = pdf_bytes.decode('latin-1', errors='ignore')
    text = re.sub(r'[^\x20-\x7E\n]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text


# ══════════════════════════════════════════════════════════════
#  BEDROCK SUMMARIZATION
# ══════════════════════════════════════════════════════════════

def summarize_with_bedrock(content, content_type):
    """
    Use Bedrock Nova Lite to generate a searchable plain-text
    description of a table or image for RAG indexing.
    """
    if content_type == 'table':
        prompt = f"""You are indexing a legal document for search.
Below is a table extracted from a legal PDF.
Write a concise plain-English description (2-4 sentences) of what
this table shows, suitable for search indexing.
Focus on the key legal information, numbers, thresholds, or criteria
it contains. Do not use markdown or bullet points.

TABLE:
{content[:1500]}

DESCRIPTION:"""
    else:
        prompt = f"""You are indexing a legal document for search.
Below is a description of an image/figure from a legal PDF.
Write a concise plain-English description (1-3 sentences) of what
this image likely shows based on the context.

IMAGE INFO:
{content}

DESCRIPTION:"""

    try:
        request_body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}]
                }
            ],
            "inferenceConfig": {
                "maxTokens":   200,
                "temperature": 0.1
            }
        }
        response = bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(request_body),
            contentType='application/json',
            accept='application/json'
        )
        response_body = json.loads(response['body'].read())
        summary = response_body['output']['message']['content'][0]['text']
        return summary.strip()
    except Exception as e:
        print(f"Bedrock summarization error: {e}")
        return content[:500]


# ══════════════════════════════════════════════════════════════
#  DOCUMENT STATUS UPDATE
# ══════════════════════════════════════════════════════════════

def update_document_status(document_id, filename, category,
                            s3_key, chunk_count):
    """Update or create document metadata in legal-aid-documents."""
    try:
        doc_table.put_item(Item={
            'documentId':  document_id,
            'filename':    filename,
            'category':    category,
            's3Key':       s3_key,
            'status':      'indexed',
            'chunkCount':  chunk_count,
            'source_type': 'curated',
            'uploadedAt':  datetime.now(timezone.utc).isoformat(),
            'uploadedBy':  'system'
        })
        print(f"Document metadata updated: {document_id} "
              f"({chunk_count} chunks)")
    except Exception as e:
        print(f"Failed to update document status: {e}")


# ══════════════════════════════════════════════════════════════
#  HELPERS (unchanged from original)
# ══════════════════════════════════════════════════════════════

def get_category_from_key(key):
    for prefix, category in CATEGORY_MAP.items():
        if key.startswith(prefix + '/'):
            return category
    return None


def read_s3_file(bucket, key):
    response = s3.get_object(Bucket=bucket, Key=key)
    return response['Body'].read()


def split_into_chunks(text, chunk_size, overlap):
    """
    Paragraph-Aware Adaptive Chunking

    Strategy:
    1. Preserve paragraph boundaries whenever possible.
    2. Build chunks by combining complete paragraphs.
    3. Only split an oversized paragraph into sentences.
    4. Ignore overlap (parameter retained for compatibility).
    """

    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Remove trailing spaces while preserving paragraph breaks
    lines = [line.strip() for line in text.split('\n')]

    paragraphs = []
    current = []

    # Build paragraphs
    for line in lines:
        if line == "":
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(line)

    if current:
        paragraphs.append(" ".join(current))

    # Fallback if no paragraph breaks exist
    if not paragraphs:
        paragraphs = [re.sub(r'\s+', ' ', text).strip()]

    chunks = []
    current_chunk = []
    current_word_count = 0

    for para in paragraphs:

        para = re.sub(r'\s+', ' ', para).strip()

        if not para:
            continue

        para_words = para.split()
        para_len = len(para_words)

        # Skip tiny paragraphs
        if para_len < 5:
            continue

        # ---------------------------------------------------------
        # CASE 1: Paragraph itself is larger than target chunk size
        # ---------------------------------------------------------
        if para_len > chunk_size:

            # Flush existing chunk first
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_word_count = 0

            # Split paragraph by sentences
            sentences = re.split(r'(?<=[.!?])\s+', para)

            sentence_chunk = []
            sentence_count = 0

            for sentence in sentences:

                sentence_words = sentence.split()
                sentence_len = len(sentence_words)

                if sentence_count + sentence_len <= chunk_size:
                    sentence_chunk.append(sentence)
                    sentence_count += sentence_len
                else:
                    if sentence_chunk:
                        chunks.append(" ".join(sentence_chunk))
                    sentence_chunk = [sentence]
                    sentence_count = sentence_len

            if sentence_chunk:
                chunks.append(" ".join(sentence_chunk))

            continue

        # ---------------------------------------------------------
        # CASE 2: Add paragraph to current chunk
        # ---------------------------------------------------------
        if current_word_count + para_len <= chunk_size:
            current_chunk.append(para)
            current_word_count += para_len

        else:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))

            current_chunk = [para]
            current_word_count = para_len

    # Last chunk
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    # Remove extremely tiny chunks
    final_chunks = []

    for chunk in chunks:
        if len(chunk.split()) >= 20:
            final_chunks.append(chunk)

        elif final_chunks:
            final_chunks[-1] += "\n\n" + chunk

    return final_chunks

def extract_keywords(text, category):
    text_lower = text.lower()
    words      = re.findall(r'\b[a-z]{3,}\b', text_lower)
    stopwords  = {
        'the','and','for','are','but','not','you','all','can',
        'has','her','was','one','our','out','day','get','him',
        'his','how','its','may','who','also','been','from',
        'have','into','more','only','over','said','such','than',
        'that','their','them','then','there','they','this','were',
        'will','with','under','shall','upon','each','any','which',
        'where','when','what','section','provided','order','made'
    }
    legal_keywords = [
        kw for kw in CATEGORY_KEYWORDS.get(category, [])
        if kw in text_lower
    ]
    word_freq = {}
    for word in words:
        if word not in stopwords and len(word) > 3:
            word_freq[word] = word_freq.get(word, 0) + 1
    frequent = sorted(word_freq, key=word_freq.get, reverse=True)[:10]
    all_keywords = list(dict.fromkeys(legal_keywords + frequent))
    return all_keywords[:15]


def delete_existing_chunks(category, source_file, document_id):
    try:
        # Delete by category + sourceFile (existing GSI)
        response = table.query(
            IndexName='sourceFile-chunkIndex-index',
            KeyConditionExpression=boto3.dynamodb.conditions
                .Key('sourceFile').eq(source_file)
        )
        existing = response['Items']
        if existing:
            with table.batch_writer() as batch:
                for item in existing:
                    batch.delete_item(Key={
                        'category': item['category'],
                        'chunkId':  item['chunkId']
                    })
            print(f"Deleted {len(existing)} existing chunks "
                  f"for {source_file}")
    except Exception as e:
        print(f"Warning: could not delete existing chunks: {e}")