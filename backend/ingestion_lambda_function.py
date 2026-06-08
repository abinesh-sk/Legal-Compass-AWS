import json
import boto3
import os
import re
from datetime import datetime, timezone

# ── Configuration from environment variables ──────────────────
CHUNKS_TABLE  = os.environ.get('CHUNKS_TABLE_NAME', 'legal-aid-chunks')
BUCKET_NAME   = os.environ.get('S3_BUCKET_NAME', '')
REGION        = os.environ.get('REGION', 'us-east-1')
CHUNK_SIZE    = int(os.environ.get('CHUNK_SIZE', '300'))
CHUNK_OVERLAP = int(os.environ.get('CHUNK_OVERLAP', '50'))

# ── AWS clients (initialized once, reused across invocations) ──
s3        = boto3.client('s3', region_name=REGION)
dynamodb  = boto3.resource('dynamodb', region_name=REGION)
table     = dynamodb.Table(CHUNKS_TABLE)

# ── Category mapping: S3 folder prefix → DynamoDB category ────
CATEGORY_MAP = {
    'tenant-rights':      'tenant-rights',
    'employment-rights':  'employment-rights',
    'government-benefits':'government-benefits',
    'immigration':        'immigration',
}

# ── Legal keywords per category for scoring ───────────────────
CATEGORY_KEYWORDS = {
    'tenant-rights': [
        'eviction', 'tenant', 'landlord', 'rent', 'lease', 'deposit',
        'notice', 'property', 'agreement', 'court', 'remove', 'vacate',
        'rental', 'premises', 'termination', 'repair', 'maintenance'
    ],
    'employment-rights': [
        'employee', 'employer', 'salary', 'wage', 'termination', 'contract',
        'dismissal', 'notice', 'compensation', 'leave', 'maternity',
        'discrimination', 'harassment', 'overtime', 'provident', 'gratuity'
    ],
    'government-benefits': [
        'pension', 'disability', 'welfare', 'scheme', 'benefit', 'allowance',
        'subsidy', 'ration', 'card', 'eligibility', 'application', 'income',
        'poverty', 'assistance', 'grant', 'insurance'
    ],
    'immigration': [
        'visa', 'passport', 'citizenship', 'asylum', 'refugee', 'permit',
        'immigration', 'deportation', 'nationality', 'resident', 'foreign',
        'border', 'documentation', 'status', 'application', 'renewal'
    ]
}


# ══════════════════════════════════════════════════════════════
#  MAIN HANDLER
# ══════════════════════════════════════════════════════════════

def lambda_handler(event, context):
    """
    Two trigger modes:
    1. Manual test  → event = {"action": "ingest_all"}
    2. S3 trigger   → event contains S3 bucket/key info
    """
    print(f"Ingestion Lambda started. Event: {json.dumps(event)}")
    
    results = {
        'files_processed': 0,
        'chunks_created':  0,
        'errors':          []
    }

    # ── Determine trigger mode ─────────────────────────────────
    if 'Records' in event and event['Records'][0].get('eventSource') == 'aws:s3':
        # S3 trigger — process only the uploaded file
        files_to_process = []
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key    = record['s3']['object']['key']
            files_to_process.append((bucket, key))
        print(f"S3 trigger mode: processing {len(files_to_process)} file(s)")
    else:
        # Manual mode — process ALL files in bucket
        files_to_process = list_all_s3_files(BUCKET_NAME)
        print(f"Manual mode: found {len(files_to_process)} files to process")

    # ── Process each file ──────────────────────────────────────
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

    print(f"Ingestion complete: {json.dumps(results)}")
    
    return {
        'statusCode': 200,
        'body': json.dumps(results)
    }


# ══════════════════════════════════════════════════════════════
#  FILE LISTING
# ══════════════════════════════════════════════════════════════

def list_all_s3_files(bucket):
    """List every .txt and .pdf file in the bucket."""
    files = []
    paginator = s3.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            key = obj['Key']
            # Skip folder markers and unsupported types
            if key.endswith('/'):
                continue
            if not (key.endswith('.txt') or key.endswith('.pdf')):
                continue
            files.append((bucket, key))
    
    return files


# ══════════════════════════════════════════════════════════════
#  FILE PROCESSING
# ══════════════════════════════════════════════════════════════

def process_file(bucket, key):
    """
    Full pipeline for one file:
    1. Determine category from S3 folder prefix
    2. Read the file content from S3
    3. Delete existing chunks for this file (clean re-ingestion)
    4. Split text into chunks
    5. Write each chunk to DynamoDB
    """
    # ── Determine category ─────────────────────────────────────
    category = get_category_from_key(key)
    if not category:
        print(f"Skipping {key} — no matching category folder")
        return {'chunks_created': 0}

    # ── Read file content ──────────────────────────────────────
    text = read_s3_file(bucket, key)
    if not text or len(text.strip()) < 50:
        print(f"Skipping {key} — file is empty or too short")
        return {'chunks_created': 0}

    # ── Clean filename for use as sourceFile ───────────────────
    source_file = key.split('/')[-1]  # "tenant-rights/guide.txt" → "guide.txt"

    # ── Delete existing chunks for this file ───────────────────
    delete_existing_chunks(category, source_file)

    # ── Split into chunks ──────────────────────────────────────
    chunks = split_into_chunks(text, CHUNK_SIZE, CHUNK_OVERLAP)

    # ── Write each chunk to DynamoDB ───────────────────────────
    chunks_created = 0
    with table.batch_writer() as batch:
        for i, chunk_text in enumerate(chunks):
            chunk_id  = f"{category}-{source_file}-{i:04d}"
            keywords  = extract_keywords(chunk_text, category)
            
            item = {
                'category':   category,
                'chunkId':    chunk_id,
                'content':    chunk_text,
                'sourceFile': source_file,
                'keywords':   keywords,
                'chunkIndex': i,
                'createdAt':  datetime.now(timezone.utc).isoformat(),
                'wordCount':  len(chunk_text.split())
            }
            batch.put_item(Item=item)
            chunks_created += 1

    return {'chunks_created': chunks_created}


# ══════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════

def get_category_from_key(key):
    """
    Map S3 key prefix to category.
    "tenant-rights/guide.txt" → "tenant-rights"
    """
    for prefix, category in CATEGORY_MAP.items():
        if key.startswith(prefix + '/'):
            return category
    return None


def read_s3_file(bucket, key):
    """Read a .txt or .pdf file from S3 and return plain text."""
    response = s3.get_object(Bucket=bucket, Key=key)
    content  = response['Body'].read()

    if key.endswith('.txt'):
        return content.decode('utf-8', errors='ignore')

    elif key.endswith('.pdf'):
        return extract_text_from_pdf_bytes(content)

    return ''


def extract_text_from_pdf_bytes(pdf_bytes):
    """
    Extract text from PDF using PyMuPDF (fitz).
    Handles compressed PDFs, custom encodings, and
    complex layouts including Indian government documents.
    """
    try:
        import fitz  # PyMuPDF

        # Open PDF from bytes
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        extracted_pages = []

        for page_num in range(len(doc)):
            page = doc[page_num]

            # Extract text with layout preservation
            text = page.get_text("text")

            # Clean up the text
            # Remove excessive whitespace
            lines = [line.strip() for line in text.split('\n')]
            # Remove empty lines and very short lines (page numbers etc)
            lines = [line for line in lines if len(line) > 3]
            page_text = ' '.join(lines)

            if page_text.strip():
                extracted_pages.append(page_text)

        doc.close()

        full_text = '\n\n'.join(extracted_pages)
        print(f"PyMuPDF extracted {len(full_text)} characters from PDF")

        # Verify extraction quality
        if len(full_text.strip()) < 100:
            print("Warning: Very little text extracted - PDF may be scanned/image-based")
            return full_text

        return full_text

    except ImportError:
        print("PyMuPDF not available - falling back to basic extraction")
        return extract_text_basic_fallback(pdf_bytes)

    except Exception as e:
        print(f"PyMuPDF extraction error: {e}")
        return extract_text_basic_fallback(pdf_bytes)


def extract_text_basic_fallback(pdf_bytes):
    """
    Fallback extractor if PyMuPDF fails.
    Better than the original but still limited.
    """
    import re
    try:
        # Try UTF-8 first
        text = pdf_bytes.decode('utf-8', errors='ignore')
    except Exception:
        text = pdf_bytes.decode('latin-1', errors='ignore')

    # Remove binary garbage - keep only readable ASCII
    text = re.sub(r'[^\x20-\x7E\n\r\t]', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove very short tokens (binary artifacts)
    words = [w for w in text.split() if len(w) > 1]
    return ' '.join(words)


def split_into_chunks(text, chunk_size, overlap):
    """
    Split text into overlapping word-based chunks.
    
    Example with chunk_size=5, overlap=2:
    Text: "A B C D E F G H I J"
    Chunks: ["A B C D E", "D E F G H", "G H I J"]
    
    The overlap ensures context is not lost at chunk boundaries.
    """
    # Clean the text first
    text = re.sub(r'\s+', ' ', text).strip()
    words = text.split()
    
    if not words:
        return []
    
    chunks = []
    start  = 0
    
    while start < len(words):
        end        = min(start + chunk_size, len(words))
        chunk_text = ' '.join(words[start:end])
        
        # Only keep chunks with meaningful content (at least 20 words)
        if len(words[start:end]) >= 20:
            chunks.append(chunk_text)
        
        # Move forward by (chunk_size - overlap)
        start += (chunk_size - overlap)
        
        # Safety: avoid infinite loop on very short texts
        if start >= len(words):
            break
    
    return chunks


def extract_keywords(text, category):
    """
    Extract relevant keywords from chunk text.
    Combines category-specific legal terms found in the text
    with the most frequent non-common words.
    """
    text_lower = text.lower()
    words      = re.findall(r'\b[a-z]{3,}\b', text_lower)
    
    # Common English words to ignore
    stopwords = {
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all',
        'can', 'has', 'her', 'was', 'one', 'our', 'out', 'day',
        'get', 'has', 'him', 'his', 'how', 'its', 'may', 'who',
        'also', 'been', 'from', 'have', 'into', 'more', 'only',
        'over', 'said', 'such', 'than', 'that', 'their', 'them',
        'then', 'there', 'they', 'this', 'were', 'will', 'with',
        'under', 'shall', 'upon', 'each', 'any', 'which', 'where',
        'when', 'what', 'section', 'provided', 'order', 'made'
    }
    
    # Find category-specific legal keywords present in this chunk
    legal_keywords = [
        kw for kw in CATEGORY_KEYWORDS.get(category, [])
        if kw in text_lower
    ]
    
    # Find frequent non-stopword words
    word_freq = {}
    for word in words:
        if word not in stopwords and len(word) > 3:
            word_freq[word] = word_freq.get(word, 0) + 1
    
    # Top 10 frequent words
    frequent = sorted(word_freq, key=word_freq.get, reverse=True)[:10]
    
    # Combine legal keywords + frequent words, deduplicated
    all_keywords = list(dict.fromkeys(legal_keywords + frequent))
    
    return all_keywords[:15]  # DynamoDB String Set limit


def delete_existing_chunks(category, source_file):
    """
    Delete all existing chunks for a given sourceFile
    before re-ingesting. Uses the GSI we created on Day 6.
    """
    try:
        response = table.query(
            IndexName='sourceFile-chunkIndex-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('sourceFile').eq(source_file)
        )
        
        existing_items = response['Items']
        
        if existing_items:
            with table.batch_writer() as batch:
                for item in existing_items:
                    batch.delete_item(
                        Key={
                            'category': item['category'],
                            'chunkId':  item['chunkId']
                        }
                    )
            print(f"Deleted {len(existing_items)} existing chunks for {source_file}")
    
    except Exception as e:
        print(f"Warning: could not delete existing chunks for {source_file}: {e}")