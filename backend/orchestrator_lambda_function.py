import json
import boto3
import os
import uuid
import time
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────
REGION              = os.environ.get('REGION', 'us-east-1')
MODEL_ID            = os.environ.get('BEDROCK_MODEL_ID', 'us.amazon.nova-lite-v1:0')
CHUNKS_TABLE        = os.environ.get('CHUNKS_TABLE', 'legal-aid-chunks')
CONVERSATIONS_TABLE = os.environ.get('CONVERSATIONS_TABLE', 'legal-aid-conversations')
MAX_HISTORY_TURNS   = int(os.environ.get('MAX_HISTORY_TURNS', '5'))
TOP_CHUNKS          = int(os.environ.get('TOP_CHUNKS', '3'))
LEX_BOT_ID   = os.environ.get('LEX_BOT_ID', '')
LEX_ALIAS_ID = os.environ.get('LEX_ALIAS_ID', '')
LEX_LOCALE   = os.environ.get('LEX_LOCALE_ID', 'en_US')

# ── AWS clients ────────────────────────────────────────────────
bedrock   = boto3.client('bedrock-runtime', region_name=REGION)
dynamodb  = boto3.resource('dynamodb',      region_name=REGION)
translate = boto3.client('translate',       region_name=REGION)
chunks_table = dynamodb.Table(CHUNKS_TABLE)
conv_table   = dynamodb.Table(CONVERSATIONS_TABLE)
lex = boto3.client('lexv2-runtime', region_name=REGION)

# ── Category keywords for intent detection ────────────────────
INTENT_KEYWORDS = {
    'tenant-rights': [
        'landlord', 'tenant', 'rent', 'evict', 'eviction', 'lease',
        'deposit', 'notice', 'property', 'house', 'flat', 'apartment',
        'rental', 'vacate', 'repair', 'maintenance', 'agreement'
    ],
    'employment-rights': [
        'employer', 'employee', 'job', 'salary', 'fired', 'terminate',
        'termination', 'work', 'office', 'wage', 'leave', 'maternity',
        'overtime', 'provident', 'gratuity', 'dismiss', 'resign'
    ],
    'government-benefits': [
        'scheme', 'benefit', 'government', 'pension', 'welfare', 'ration',
        'subsidy', 'card', 'allowance', 'insurance', 'hospital', 'health',
        'ayushman', 'jan dhan', 'disability', 'poverty', 'bpl'
    ],
    'immigration': [
        'visa', 'passport', 'citizen', 'citizenship', 'asylum', 'refugee',
        'permit', 'foreign', 'immigration', 'deportation', 'nationality',
        'oci', 'nri', 'border', 'travel', 'document', 'status'
    ]
}


# ══════════════════════════════════════════════════════════════
#  MAIN HANDLER
# ══════════════════════════════════════════════════════════════

def lambda_handler(event, context):
    print(f"Orchestrator received event: {json.dumps(event)}")

    # ── Parse input ────────────────────────────────────────────
    # Handle both direct invocation and API Gateway format
    if 'body' in event:
        # Coming from API Gateway
        try:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        except Exception:
            body = {}
    else:
        # Direct Lambda invocation (testing)
        body = event

    message    = body.get('message', '').strip()
    session_id = body.get('sessionId', str(uuid.uuid4()))

    if not message:
        return format_response(400, {
            'error': 'Message is required',
            'sessionId': session_id
        })

    print(f"Session: {session_id} | Message: {message}")

    try:
        # ── STEP 1: Detect language + translate to English ─────
        detected_language, english_message = translate_to_english(message)
        print(f"Language detected: {detected_language}")
        print(f"English message: {english_message}")

        # ── STEP 2: Detect intent category ────────────────────
        category = detect_intent(english_message, session_id)
        print(f"Intent category: {category}")

        # ── STEP 3: Search DynamoDB for relevant chunks ────────
        chunks    = fetch_chunks(category)
        top_chunks = rank_chunks(chunks, english_message)[:TOP_CHUNKS]
        print(f"Found {len(chunks)} chunks, using top {len(top_chunks)}")

        # ── STEP 4: Load conversation history ─────────────────
        history = load_history(session_id)
        print(f"Loaded {len(history)} history turns")

        # ── STEP 5: Build RAG prompt ───────────────────────────
        prompt = build_prompt(english_message, top_chunks, history)

        # ── STEP 6: Call Nova Lite ─────────────────────────────
        english_answer, usage = call_bedrock(prompt)
        print(f"Answer generated. Tokens: {usage}")

        # ── STEP 7: Save conversation turn ────────────────────
        save_conversation(session_id, message, english_answer,
                         detected_language, category)

        # ── STEP 8: Translate answer back to user language ─────
        final_answer = translate_to_user_language(
            english_answer, detected_language
        )

        # ── Return response ────────────────────────────────────
        return format_response(200, {
            'answer':           final_answer,
            'sessionId':        session_id,
            'detectedLanguage': detected_language,
            'category':         category,
            'sources':          list(set(
                                    c.get('sourceFile', '')
                                    for c in top_chunks
                                )),
            'tokenUsage':       usage
        })

    except Exception as e:
        print(f"Orchestrator error: {str(e)}")
        import traceback
        traceback.print_exc()
        return format_response(500, {
            'error':     'Something went wrong. Please try again.',
            'sessionId': session_id,
            'detail':    str(e)
        })


# ══════════════════════════════════════════════════════════════
#  STEP 1: LANGUAGE DETECTION + TRANSLATION
# ══════════════════════════════════════════════════════════════

def translate_to_english(text):
    """
    Detect language and translate to English.
    If already English, return as-is.
    """
    try:
        response = translate.translate_text(
            Text=text,
            SourceLanguageCode='auto',
            TargetLanguageCode='en'
        )

        # Print full response for debugging
        print(f"Translate response keys: {list(response.keys())}")

        # Try different possible key names
        detected = (
            response.get('AppliedSourceLanguageCode') or
            response.get('SourceLanguageCode') or
            'en'
        )
        translated = response.get('TranslatedText', text)

        return detected, translated

    except Exception as e:
        print(f"Translation error: {str(e)}")
        print(f"Full error: {repr(e)}")
        return 'en', text


def translate_to_user_language(text, target_language):
    """
    Translate answer back to user's original language.
    Keeps legal document names in English.
    """
    if target_language == 'en':
        return text

    try:
        # Extract source citations before translation
        # "Source: document.pdf" should stay in English
        source_line = ""
        main_text   = text

        if "Source:" in text:
            parts     = text.rsplit("Source:", 1)
            main_text = parts[0].strip()
            source_line = "\nSource:" + parts[1]

        # Translate only the main answer text
        response = translate.translate_text(
            Text=main_text,
            SourceLanguageCode='en',
            TargetLanguageCode=target_language
        )
        translated = response['TranslatedText']

        # Re-attach source citation in English
        return translated + source_line

    except Exception as e:
        print(f"Back-translation error: {e}")
        return text


# ══════════════════════════════════════════════════════════════
#  STEP 2: INTENT DETECTION
# ══════════════════════════════════════════════════════════════

def detect_intent(text, session_id='default-session'):
    """
    Use Amazon Lex V2 to detect intent category.
    Falls back to keyword matching if Lex fails or
    returns low confidence.
    """
    # Try Lex first
    if LEX_BOT_ID and LEX_ALIAS_ID:
        try:
            response = lex.recognize_text(
                botId=LEX_BOT_ID,
                botAliasId=LEX_ALIAS_ID,
                localeId=LEX_LOCALE,
                sessionId=session_id,
                text=text
            )

            interpretations = response.get('interpretations', [])

            if interpretations:
                top = interpretations[0]
                intent_name = top['intent']['name']
                confidence  = top.get('nluConfidence', {}).get('score', 0)

                print(f"Lex intent: {intent_name} | Confidence: {confidence}")

                # Map Lex intent names to our category names
                intent_map = {
                    'TenantRightsIntent':       'tenant-rights',
                    'EmploymentRightsIntent':    'employment-rights',
                    'GovernmentBenefitsIntent':  'government-benefits',
                    'ImmigrationIntent':         'immigration',
                    'FallbackIntent':            None
                }

                category = intent_map.get(intent_name)

                # Use Lex result if confidence is good
                # and it's not a fallback
                if category and confidence >= 0.4:
                    print(f"Lex classification: {category} ({confidence:.2f})")
                    return category
                else:
                    print(f"Lex low confidence or fallback — using keyword matching")

        except Exception as e:
            print(f"Lex error: {str(e)} — falling back to keywords")

    # Fallback: keyword matching (our original approach)
    return detect_intent_keywords(text)


def detect_intent_keywords(text):
    """
    Original keyword-based intent detection.
    Used as fallback when Lex is unavailable or
    returns low confidence.
    """
    text_lower = text.lower()
    scores     = {}

    for category, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        scores[category] = score

    print(f"Keyword intent scores: {scores}")

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else 'tenant-rights'


# ══════════════════════════════════════════════════════════════
#  STEP 3: FETCH + RANK CHUNKS
# ══════════════════════════════════════════════════════════════

def fetch_chunks(category):
    """Query DynamoDB for chunks, with general category fallback."""
    try:
        response = chunks_table.query(
            KeyConditionExpression=Key('category').eq(category)
        )
        items = response.get('Items', [])
        
        # If no chunks found in detected category, also search general
        if len(items) < 3:
            general_response = chunks_table.query(
                KeyConditionExpression=Key('category').eq('general')
            )
            general_items = general_response.get('Items', [])
            items = items + general_items
            print(f"Added {len(general_items)} general chunks as fallback")
        
        return items
    except Exception as e:
        print(f"DynamoDB fetch error: {e}")
        return []


def rank_chunks(chunks, question):
    """Score chunks by keyword overlap with the question."""
    stopwords = {
        'the','and','for','are','but','not','you','all','can',
        'has','was','one','how','its','may','who','also','been',
        'from','have','into','more','only','over','than','that',
        'them','then','they','this','were','will','with','what',
        'when','where','which','does','your','about','my','is',
        'it','in','of','to','a','an','do','be','at','or','me',
        'if','as','by','on','up','so','no','we','he','she','his'
    }
    question_words = set(
        w.lower() for w in question.split()
        if w.lower() not in stopwords and len(w) > 2
    )

    scored = []
    for chunk in chunks:
        content_lower  = chunk.get('content', '').lower()
        chunk_keywords = set(chunk.get('keywords', []))
        content_score  = sum(1 for w in question_words
                            if w in content_lower)
        keyword_score  = len(question_words & chunk_keywords)
        scored.append({
            **chunk,
            'score': content_score + (keyword_score * 2)
        })

    return sorted(scored, key=lambda x: x['score'], reverse=True)


# ══════════════════════════════════════════════════════════════
#  STEP 4: CONVERSATION HISTORY
# ══════════════════════════════════════════════════════════════

def load_history(session_id):
    """
    Load last N conversation turns for this session.
    Returns list of {role, message} dicts.
    """
    try:
        response = conv_table.query(
            KeyConditionExpression=Key('sessionId').eq(session_id),
            ScanIndexForward=False,  # newest first
            Limit=MAX_HISTORY_TURNS * 2  # user + assistant per turn
        )
        items = response.get('Items', [])
        # Reverse to get chronological order
        items.reverse()
        return [
            {
                'role':    item.get('role', 'user'),
                'message': item.get('message', '')
            }
            for item in items
        ]
    except Exception as e:
        print(f"History load error: {e}")
        return []


def save_conversation(session_id, user_message,
                     assistant_answer, language, category):
    """
    Save both the user message and assistant answer
    to DynamoDB with a 24-hour TTL.
    """
    ttl = int(time.time()) + 86400  # 24 hours from now
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Save user message
        conv_table.put_item(Item={
            'sessionId': session_id,
            'timestamp': f"{now}-user",
            'role':      'user',
            'message':   user_message,
            'language':  language,
            'category':  category,
            'ttl':       ttl
        })

        # Save assistant response
        conv_table.put_item(Item={
            'sessionId': session_id,
            'timestamp': f"{now}-assistant",
            'role':      'assistant',
            'message':   assistant_answer,
            'language':  'en',
            'category':  category,
            'ttl':       ttl
        })

        print(f"Conversation saved for session {session_id}")

    except Exception as e:
        print(f"Conversation save error: {e}")
        # Don't fail the request if history save fails


# ══════════════════════════════════════════════════════════════
#  STEP 5: BUILD RAG PROMPT
# ══════════════════════════════════════════════════════════════

def build_prompt(question, top_chunks, history):
    """
    Build a grounded RAG prompt that includes:
    - Legal document passages (from DynamoDB)
    - Conversation history (for context continuity)
    - The current question
    """
    # Format document passages
    passages = ""
    for i, chunk in enumerate(top_chunks, 1):
        passages += f"""
PASSAGE {i} (from {chunk.get('sourceFile', 'legal document')}):
{chunk.get('content', '')}
"""

    # Format conversation history
    history_text = ""
    if history:
        history_text = "\nPREVIOUS CONVERSATION:\n"
        for turn in history[-6:]:  # Last 3 exchanges
            role = "User" if turn['role'] == 'user' else "Assistant"
            history_text += f"{role}: {turn['message']}\n"
        history_text += "\n"

    return f"""You are a legal aid assistant helping low-income individuals
understand their legal rights. Answer in simple, clear language
that anyone can understand — avoid complex legal jargon.

IMPORTANT RULES:
1. Answer ONLY using the legal passages provided below
2. If the answer is not clearly in the passages, say exactly:
   "I don't have specific information about that in my documents.
    Please consult a legal professional or legal aid organization."
3. Do not invent laws, section numbers, or legal provisions
4. Keep your answer to 3-5 sentences maximum
5. Be direct — start with a clear yes/no/here's what the law says
6. End with: \"Source: \" followed by the actual document filename from the passage

LEGAL DOCUMENT PASSAGES:
{passages}
{history_text}
CURRENT QUESTION: {question}

ANSWER:"""


# ══════════════════════════════════════════════════════════════
#  STEP 6: CALL BEDROCK
# ══════════════════════════════════════════════════════════════

def call_bedrock(prompt):
    """Call Amazon Nova Lite with the RAG prompt."""
    request_body = {
        "messages": [
            {
                "role": "user",
                "content": [{"text": prompt}]
            }
        ],
        "system": [
            {
                "text": "You are a helpful legal aid assistant. Answer only from the provided passages. Be concise and clear."
            }
        ],
        "inferenceConfig": {
            "maxTokens":   500,
            "temperature": 0.1,
            "topP":        0.9
        }
    }

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(request_body),
        contentType='application/json',
        accept='application/json'
    )

    response_body  = json.loads(response['body'].read())
    answer         = response_body['output']['message']['content'][0]['text']
    input_tokens   = response_body['usage']['inputTokens']
    output_tokens  = response_body['usage']['outputTokens']

    usage = {
        'inputTokens':      input_tokens,
        'outputTokens':     output_tokens,
        'estimatedCostUsd': round(
            (input_tokens  / 1_000_000 * 0.06) +
            (output_tokens / 1_000_000 * 0.24),
            6
        )
    }
    return answer, usage


# ══════════════════════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════════════════════

def format_response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type':                'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers':'Content-Type,Authorization,X-Amz-Date,X-Amz-Security-Token,X-Amz-Content-Sha256',
            'Access-Control-Allow-Methods':'POST,OPTIONS'
        },
        'body': json.dumps(body, ensure_ascii=False)
    }