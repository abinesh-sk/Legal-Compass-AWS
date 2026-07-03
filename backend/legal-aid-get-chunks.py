import json
import boto3
import os
from boto3.dynamodb.conditions import Key

REGION       = os.environ.get('REGION', 'us-east-1')
CHUNKS_TABLE = os.environ.get('CHUNKS_TABLE', 'legal-aid-chunks')

dynamodb    = boto3.resource('dynamodb', region_name=REGION)
chunk_table = dynamodb.Table(CHUNKS_TABLE)

def lambda_handler(event, context):
    print(f"Get chunks request: {json.dumps(event)}")

    # Get documentId from query string parameters
    params     = event.get('queryStringParameters') or {}
    documentId = params.get('documentId', '').strip()

    if not documentId:
        return format_response(400, {
            'error': 'documentId query parameter is required'
        })

    try:
        # Query by documentId using the GSI
        response = chunk_table.query(
            IndexName='documentId-createdAt-index',
            KeyConditionExpression=Key('documentId').eq(documentId)
        )
        chunks = response.get('Items', [])

        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = chunk_table.query(
                IndexName='documentId-chunkIndex-index',
                KeyConditionExpression=Key('documentId').eq(documentId),
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            chunks.extend(response.get('Items', []))

        # Sort by chunkIndex
        chunks.sort(key=lambda x: str(x.get('chunkIndex', '0')))

        # Format for frontend
        formatted = []
        for chunk in chunks:
            formatted.append({
                'chunkId':    chunk.get('chunkId', ''),
                'chunkType':  chunk.get('chunk_type', 'text'),
                'content':    chunk.get('content', ''),
                'summary':    chunk.get('summary', ''),
                'sourceFile': chunk.get('sourceFile', ''),
                'wordCount':  int(chunk.get('wordCount', 0)),
                'keywords':   list(chunk.get('keywords', []))
            })

        return format_response(200, {
            'documentId': documentId,
            'chunks':     formatted,
            'count':      len(formatted)
        })

    except Exception as e:
        print(f"Error fetching chunks: {str(e)}")
        import traceback
        traceback.print_exc()
        return format_response(500, {
            'error':  'Failed to fetch chunks',
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