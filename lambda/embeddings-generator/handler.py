"""
Embeddings Generator - Generate vector embeddings using AWS Bedrock
Triggered by PDF processor, creates embeddings and stores in OpenSearch
"""

import json
import boto3
import os
from datetime import datetime
from decimal import Decimal

# AWS Clients
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')
opensearch_client = boto3.client('opensearchserverless')

# Environment variables
METADATA_TABLE = os.environ.get('METADATA_TABLE', 'pdf-metadata')
OPENSEARCH_ENDPOINT = os.environ.get('OPENSEARCH_ENDPOINT', '')
OPENSEARCH_INDEX = os.environ.get('OPENSEARCH_INDEX', 'pdf-embeddings')

# Bedrock model IDs
EMBEDDING_MODEL = 'amazon.titan-embed-text-v1'  # Free tier eligible


def lambda_handler(event, context):
    """
    Generate embeddings for document chunks
    """
    print(f"Event: {json.dumps(event)}")
    
    try:
        # Get document ID and chunks from event
        doc_id = event.get('doc_id')
        chunks = event.get('chunks', [])
        
        if not doc_id or not chunks:
            raise ValueError("Missing doc_id or chunks in event")
        
        print(f"Generating embeddings for {doc_id}: {len(chunks)} chunks")
        
        # Generate embeddings for all chunks
        embeddings_data = []
        
        for chunk in chunks:
            chunk_id = chunk['chunk_id']
            text = chunk['text']
            
            # Skip very short chunks
            if len(text.strip()) < 20:
                print(f"  Skipping short chunk {chunk_id}")
                continue
            
            # Generate embedding
            embedding = generate_embedding(text)
            
            embeddings_data.append({
                'doc_id': doc_id,
                'chunk_id': chunk_id,
                'text': text,
                'embedding': embedding,
                'start_char': chunk.get('start_char', 0),
                'end_char': chunk.get('end_char', 0)
            })
            
            print(f"  ✓ Chunk {chunk_id}: embedding generated ({len(embedding)} dims)")
        
        # Store embeddings in OpenSearch
        if OPENSEARCH_ENDPOINT:
            store_in_opensearch(doc_id, embeddings_data)
        else:
            print("⚠️ OpenSearch endpoint not configured, skipping vector storage")
        
        # Update document status in DynamoDB
        update_embedding_status(doc_id, len(embeddings_data))
        
        print(f"✓ Embeddings generated successfully: {len(embeddings_data)} vectors")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'doc_id': doc_id,
                'embeddings_created': len(embeddings_data)
            })
        }
        
    except Exception as e:
        print(f"Error generating embeddings: {str(e)}")
        raise


def generate_embedding(text):
    """
    Generate embedding vector using Bedrock Titan
    """
    try:
        # Truncate text if too long (Titan limit: 8k tokens ≈ 32k chars)
        max_chars = 30000
        if len(text) > max_chars:
            text = text[:max_chars]
            print(f"  ⚠️ Text truncated to {max_chars} chars")
        
        # Call Bedrock
        payload = {
            "inputText": text
        }
        
        response = bedrock_runtime.invoke_model(
            modelId=EMBEDDING_MODEL,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(payload)
        )
        
        # Parse response
        response_body = json.loads(response['body'].read())
        embedding = response_body.get('embedding', [])
        
        if not embedding:
            raise ValueError("No embedding returned from Bedrock")
        
        return embedding
        
    except Exception as e:
        print(f"Bedrock error: {str(e)}")
        raise


def store_in_opensearch(doc_id, embeddings_data):
    """
    Store embeddings in OpenSearch vector database
    """
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from requests_aws4auth import AWS4Auth
        
        # Setup AWS auth
        credentials = boto3.Session().get_credentials()
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            os.environ.get('AWS_REGION', 'us-east-1'),
            'aoss',  # OpenSearch Serverless
            session_token=credentials.token
        )
        
        # Create OpenSearch client
        host = OPENSEARCH_ENDPOINT.replace('https://', '').replace('http://', '')
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30
        )
        
        # Create index if doesn't exist
        if not client.indices.exists(index=OPENSEARCH_INDEX):
            create_vector_index(client)
        
        # Bulk index documents
        bulk_data = []
        
        for item in embeddings_data:
            # Index action
            bulk_data.append({
                'index': {
                    '_index': OPENSEARCH_INDEX,
                    '_id': f"{doc_id}_{item['chunk_id']}"
                }
            })
            
            # Document
            bulk_data.append({
                'doc_id': doc_id,
                'chunk_id': item['chunk_id'],
                'text': item['text'],
                'embedding': item['embedding'],
                'start_char': item['start_char'],
                'end_char': item['end_char'],
                'indexed_at': datetime.utcnow().isoformat()
            })
        
        # Execute bulk insert
        if bulk_data:
            response = client.bulk(body=bulk_data, refresh=True)
            
            if response.get('errors'):
                print(f"⚠️ Some documents failed to index")
            else:
                print(f"✓ Indexed {len(embeddings_data)} vectors in OpenSearch")
        
    except ImportError:
        print("⚠️ OpenSearch libraries not available, storing in DynamoDB only")
        # Fallback: store embeddings in DynamoDB
        store_embeddings_in_dynamodb(doc_id, embeddings_data)
        
    except Exception as e:
        print(f"OpenSearch error: {str(e)}")
        # Fallback to DynamoDB
        store_embeddings_in_dynamodb(doc_id, embeddings_data)


def create_vector_index(client):
    """
    Create OpenSearch index with vector field
    """
    index_body = {
        'settings': {
            'index': {
                'knn': True,  # Enable k-NN
                'number_of_shards': 1,
                'number_of_replicas': 0
            }
        },
        'mappings': {
            'properties': {
                'doc_id': {'type': 'keyword'},
                'chunk_id': {'type': 'integer'},
                'text': {'type': 'text'},
                'embedding': {
                    'type': 'knn_vector',
                    'dimension': 1536,  # Titan embedding dimension
                    'method': {
                        'name': 'hnsw',
                        'space_type': 'cosinesimil',
                        'engine': 'nmslib'
                    }
                },
                'start_char': {'type': 'integer'},
                'end_char': {'type': 'integer'},
                'indexed_at': {'type': 'date'}
            }
        }
    }
    
    client.indices.create(index=OPENSEARCH_INDEX, body=index_body)
    print(f"✓ Created OpenSearch index: {OPENSEARCH_INDEX}")


def store_embeddings_in_dynamodb(doc_id, embeddings_data):
    """
    Fallback: Store embeddings in DynamoDB if OpenSearch unavailable
    """
    try:
        table = dynamodb.Table(METADATA_TABLE)
        
        # Get existing document
        response = table.get_item(Key={'doc_id': doc_id})
        
        if 'Item' not in response:
            print(f"⚠️ Document {doc_id} not found in DynamoDB")
            return
        
        item = response['Item']
        
        # Add embeddings to chunks
        chunks_with_embeddings = []
        
        for chunk_data in embeddings_data:
            chunk = {
                'chunk_id': chunk_data['chunk_id'],
                'text': chunk_data['text'],
                'embedding': chunk_data['embedding'],  # List of floats
                'start_char': chunk_data['start_char'],
                'end_char': chunk_data['end_char']
            }
            chunks_with_embeddings.append(chunk)
        
        # Update document
        table.update_item(
            Key={'doc_id': doc_id},
            UpdateExpression='SET chunks_with_embeddings = :chunks, embedding_status = :status',
            ExpressionAttributeValues={
                ':chunks': chunks_with_embeddings,
                ':status': 'completed'
            }
        )
        
        print(f"✓ Stored {len(chunks_with_embeddings)} embeddings in DynamoDB")
        
    except Exception as e:
        print(f"DynamoDB fallback error: {str(e)}")


def update_embedding_status(doc_id, embeddings_count):
    """
    Update document embedding status in DynamoDB
    """
    try:
        table = dynamodb.Table(METADATA_TABLE)
        
        table.update_item(
            Key={'doc_id': doc_id},
            UpdateExpression='SET embedding_status = :status, embeddings_count = :count, embeddings_generated_at = :timestamp',
            ExpressionAttributeValues={
                ':status': 'completed',
                ':count': embeddings_count,
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        print(f"✓ Updated embedding status for {doc_id}")
        
    except Exception as e:
        print(f"Failed to update status: {str(e)}")