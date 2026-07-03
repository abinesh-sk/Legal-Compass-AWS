import json
import boto3
import os
from boto3.dynamodb.conditions import Key

REGION          = os.environ.get('REGION', 'us-east-1')
DOCUMENTS_TABLE = os.environ.get('DOCUMENTS_TABLE', 'legal-aid-documents')

dynamodb = boto3.resource('dynamodb', region_name=REGION)
doc_table = dynamodb.Table(DOCUMENTS_TABLE)

def lambda_handler(event, context):
    print(f"Get documents request: {json.dumps(event)}")

    try:
        # Scan all documents
        response = doc_table.scan()
        documents = response.get('Items', [])

        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = doc_table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            documents.extend(response.get('Items', []))

        # Sort by uploadedAt descending
        documents.sort(
            key=lambda x: x.get('uploadedAt', ''),
            reverse=True
        )

        # Convert Decimal to int for JSON serialization
        for doc in documents:
            if 'chunkCount' in doc:
                doc['chunkCount'] = int(doc['chunkCount'])

        return format_response(200, {
            'documents': documents,
            'count': len(documents)
        })

    except Exception as e:
        print(f"Error fetching documents: {str(e)}")
        import traceback
        traceback.print_exc()
        return format_response(500, {
            'error': 'Failed to fetch documents',
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
        'body': json.dumps(body, ensure_ascii=False, default=str)
    }