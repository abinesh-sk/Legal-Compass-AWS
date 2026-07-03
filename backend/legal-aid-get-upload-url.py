import json
import boto3
import os
import uuid
from datetime import datetime, timezone

REGION          = os.environ.get('REGION', 'us-east-1')
S3_BUCKET       = os.environ.get('S3_BUCKET', '')
DOCUMENTS_TABLE = os.environ.get('DOCUMENTS_TABLE', 'legal-aid-documents')

s3       = boto3.client('s3', region_name=REGION)
dynamodb = boto3.resource('dynamodb', region_name=REGION)
doc_table = dynamodb.Table(DOCUMENTS_TABLE)

# Valid categories mapping to S3 prefixes
CATEGORY_MAP = {
    'tenant-rights':       'tenant-rights',
    'employment-rights':   'employment-rights',
    'government-benefits': 'government-benefits',
    'immigration':         'immigration',
    'general':             'general'
}

ALLOWED_EXTENSIONS = {'.pdf', '.txt'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def lambda_handler(event, context):
    print(f"Upload URL request: {json.dumps(event)}")

    # Parse request body
    try:
        body = json.loads(event.get('body', '{}')) \
               if isinstance(event.get('body'), str) \
               else (event.get('body') or {})
    except Exception:
        body = {}

    filename    = body.get('filename', '').strip()
    category    = body.get('category', 'general').strip()
    source_type = body.get('source_type', 'community')

    # Validate filename
    if not filename:
        return format_response(400, {'error': 'filename is required'})

    ext = '.' + filename.rsplit('.', 1)[-1].lower() \
          if '.' in filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return format_response(400, {
            'error': f'File type not supported. Allowed: PDF, TXT'
        })

    # Validate category
    if category not in CATEGORY_MAP:
        category = 'general'

    # Build S3 key and documentId
    safe_filename = filename.replace(' ', '-')
    s3_prefix     = CATEGORY_MAP[category]
    s3_key        = f"{s3_prefix}/{safe_filename}"
    document_id   = f"{s3_prefix}#{safe_filename}"

    try:
        # Generate presigned URL (valid for 5 minutes)
        presigned_url = s3.generate_presigned_url(
            'put_object',
            Params={
                'Bucket':      S3_BUCKET,
                'Key':         s3_key,
                'ContentType': 'application/pdf'
                               if ext == '.pdf'
                               else 'text/plain'
            },
            ExpiresIn=300
        )

        # Write "pending" document record to DynamoDB
        doc_table.put_item(Item={
            'documentId':  document_id,
            'filename':    safe_filename,
            'category':    category,
            's3Key':       s3_key,
            'status':      'pending',
            'chunkCount':  0,
            'source_type': source_type,
            'uploadedAt':  datetime.now(timezone.utc).isoformat(),
            'uploadedBy':  'anonymous'
        })

        return format_response(200, {
            'uploadUrl':  presigned_url,
            'documentId': document_id,
            's3Key':      s3_key,
            'message':    'Upload URL generated. PUT your file to uploadUrl.'
        })

    except Exception as e:
        print(f"Error generating upload URL: {str(e)}")
        import traceback
        traceback.print_exc()
        return format_response(500, {
            'error':  'Failed to generate upload URL',
            'detail': str(e)
        })


def format_response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-Amz-Date,X-Amz-Security-Token,X-Amz-Content-Sha256',
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
        },
        'body': json.dumps(body, ensure_ascii=False)
    }