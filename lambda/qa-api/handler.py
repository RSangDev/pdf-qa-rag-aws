"""
Q&A API - RAG (Retrieval Augmented Generation)
Search embeddings, retrieve relevant context, generate answer with Claude
"""

import json
import boto3
import os
from decimal import Decimal

# AWS Clients
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')

# Environment variables
METADATA_TABLE = os.environ.get('METADATA_TABLE', 'pdf-metadata')
OPENSEARCH_ENDPOINT = os.environ.get('OPENSEARCH_ENDPOINT', '')
OPENSEARCH_INDEX = os.environ.get('OPENSEARCH_INDEX', 'pdf-embeddings')

# Bedrock models
EMBEDDING_MODEL = 'amazon.titan-embed-text-v1'
LLM_MODEL = 'anthropic.claude-3-sonnet-20240229-v1:0'

# CORS Headers
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
}


def lambda_handler(event, context):
    """
    Main handler - routes requests
    """
    print(f"Event: {json.dumps(event)}")
    
    http_method = event.get('httpMethod', '')
    path = event.get('path', '')
    
    if http_method == 'OPTIONS':
        return cors_response(200, {'message': 'OK'})
    
    elif http_method == 'POST' and '/ask' in path:
        return handle_ask_question(event)
    
    elif http_method == 'GET' and '/documents' in path:
        return handle_list_documents(event)
    
    elif http_method == 'GET' and '/document/' in path:
        return handle_get_document(event)
    
    else:
        return cors_response(404, {'error': 'Not found'})


def handle_ask_question(event):
    """
    Answer question using RAG
    """
    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        
        question = body.get('question', '').strip()
        doc_id = body.get('doc_id')  # Optional: search specific document
        top_k = int(body.get('top_k', 5))  # Number of chunks to retrieve
        
        if not question:
            return cors_response(400, {'error': 'Question is required'})
        
        print(f"Question: {question}")
        print(f"Doc ID filter: {doc_id}")
        
        # Step 1: Generate embedding for question
        question_embedding = generate_embedding(question)
        
        # Step 2: Search for relevant chunks
        relevant_chunks = search_similar_chunks(
            question_embedding,
            doc_id=doc_id,
            top_k=top_k
        )
        
        if not relevant_chunks:
            return cors_response(200, {
                'answer': "I couldn't find relevant information in the documents to answer your question.",
                'sources': [],
                'confidence': 'low'
            })
        
        # Step 3: Build context from chunks
        context = build_context(relevant_chunks)
        
        # Step 4: Generate answer with Claude
        answer = generate_answer_with_claude(question, context)
        
        # Step 5: Format response with sources
        sources = format_sources(relevant_chunks)
        
        return cors_response(200, {
            'answer': answer,
            'sources': sources,
            'chunks_used': len(relevant_chunks),
            'confidence': 'high' if len(relevant_chunks) >= 3 else 'medium'
        })
        
    except Exception as e:
        print(f"Error in ask_question: {str(e)}")
        return cors_response(500, {'error': 'Internal server error'})


def generate_embedding(text):
    """
    Generate embedding for query using Bedrock Titan
    """
    try:
        payload = {"inputText": text}
        
        response = bedrock_runtime.invoke_model(
            modelId=EMBEDDING_MODEL,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(payload)
        )
        
        response_body = json.loads(response['body'].read())
        return response_body.get('embedding', [])
        
    except Exception as e:
        print(f"Embedding error: {str(e)}")
        raise


def search_similar_chunks(query_embedding, doc_id=None, top_k=5):
    """
    Search for similar chunks using vector similarity
    Falls back to DynamoDB if OpenSearch unavailable
    """
    try:
        if OPENSEARCH_ENDPOINT:
            return search_opensearch(query_embedding, doc_id, top_k)
        else:
            return search_dynamodb_fallback(query_embedding, doc_id, top_k)
            
    except Exception as e:
        print(f"Search error, falling back to DynamoDB: {str(e)}")
        return search_dynamodb_fallback(query_embedding, doc_id, top_k)


def search_opensearch(query_embedding, doc_id=None, top_k=5):
    """
    Search OpenSearch using k-NN vector similarity
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
            'aoss',
            session_token=credentials.token
        )
        
        # Create client
        host = OPENSEARCH_ENDPOINT.replace('https://', '').replace('http://', '')
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection
        )
        
        # Build search query
        search_body = {
            'size': top_k,
            'query': {
                'knn': {
                    'embedding': {
                        'vector': query_embedding,
                        'k': top_k
                    }
                }
            }
        }
        
        # Add document filter if specified
        if doc_id:
            search_body['query'] = {
                'bool': {
                    'must': [
                        {'knn': {'embedding': {'vector': query_embedding, 'k': top_k}}},
                        {'term': {'doc_id': doc_id}}
                    ]
                }
            }
        
        # Execute search
        response = client.search(index=OPENSEARCH_INDEX, body=search_body)
        
        # Extract results
        chunks = []
        for hit in response['hits']['hits']:
            source = hit['_source']
            chunks.append({
                'doc_id': source['doc_id'],
                'chunk_id': source['chunk_id'],
                'text': source['text'],
                'score': hit['_score']
            })
        
        print(f"✓ Found {len(chunks)} similar chunks in OpenSearch")
        return chunks
        
    except ImportError:
        print("OpenSearch libraries not available")
        raise
    except Exception as e:
        print(f"OpenSearch search error: {str(e)}")
        raise


def search_dynamodb_fallback(query_embedding, doc_id=None, top_k=5):
    """
    Fallback: Search DynamoDB using cosine similarity
    Less efficient but works without OpenSearch
    """
    try:
        table = dynamodb.Table(METADATA_TABLE)
        
        # Get all documents or specific document
        if doc_id:
            response = table.get_item(Key={'doc_id': doc_id})
            documents = [response['Item']] if 'Item' in response else []
        else:
            response = table.scan()
            documents = response.get('Items', [])
        
        # Calculate similarity for all chunks
        all_chunks_with_scores = []
        
        for doc in documents:
            chunks_with_emb = doc.get('chunks_with_embeddings', [])
            
            for chunk in chunks_with_emb:
                embedding = chunk.get('embedding', [])
                
                if embedding:
                    # Calculate cosine similarity
                    similarity = cosine_similarity(query_embedding, embedding)
                    
                    all_chunks_with_scores.append({
                        'doc_id': doc['doc_id'],
                        'chunk_id': chunk['chunk_id'],
                        'text': chunk['text'],
                        'score': similarity
                    })
        
        # Sort by similarity and get top k
        all_chunks_with_scores.sort(key=lambda x: x['score'], reverse=True)
        top_chunks = all_chunks_with_scores[:top_k]
        
        print(f"✓ Found {len(top_chunks)} similar chunks in DynamoDB")
        return top_chunks
        
    except Exception as e:
        print(f"DynamoDB search error: {str(e)}")
        return []


def cosine_similarity(vec1, vec2):
    """Calculate cosine similarity between two vectors"""
    import math
    
    # Convert Decimal to float if needed
    vec1 = [float(x) if isinstance(x, Decimal) else x for x in vec1]
    vec2 = [float(x) if isinstance(x, Decimal) else x for x in vec2]
    
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = math.sqrt(sum(a * a for a in vec1))
    magnitude2 = math.sqrt(sum(b * b for b in vec2))
    
    if magnitude1 == 0 or magnitude2 == 0:
        return 0
    
    return dot_product / (magnitude1 * magnitude2)


def build_context(chunks):
    """
    Build context string from retrieved chunks
    """
    context_parts = []
    
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(f"[Document {i}]\n{chunk['text']}\n")
    
    return '\n'.join(context_parts)


def generate_answer_with_claude(question, context):
    """
    Generate answer using Claude with RAG context
    """
    try:
        # Build prompt
        prompt = f"""You are a helpful assistant that answers questions based on the provided context from PDF documents.

Context from documents:
{context}

Question: {question}

Instructions:
- Answer the question based ONLY on the information in the context above
- If the context doesn't contain enough information to answer the question, say so
- Be concise but thorough
- Cite which document section you're referring to when relevant
- If you're unsure, express uncertainty

Answer:"""

        # Call Claude
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,  # Low temperature for factual responses
            "top_p": 0.9
        }
        
        response = bedrock_runtime.invoke_model(
            modelId=LLM_MODEL,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(payload)
        )
        
        response_body = json.loads(response['body'].read())
        
        # Extract answer
        answer = response_body.get('content', [{}])[0].get('text', '')
        
        print(f"✓ Generated answer: {answer[:100]}...")
        
        return answer
        
    except Exception as e:
        print(f"Claude error: {str(e)}")
        return f"Error generating answer: {str(e)}"


def format_sources(chunks):
    """
    Format source chunks for response
    """
    sources = []
    
    for chunk in chunks:
        sources.append({
            'doc_id': chunk['doc_id'],
            'chunk_id': chunk['chunk_id'],
            'text_preview': chunk['text'][:200] + '...' if len(chunk['text']) > 200 else chunk['text'],
            'relevance_score': round(chunk['score'], 3) if 'score' in chunk else None
        })
    
    return sources


def handle_list_documents(event):
    """
    List all uploaded documents
    """
    try:
        table = dynamodb.Table(METADATA_TABLE)
        
        response = table.scan()
        documents = response.get('Items', [])
        
        # Format documents
        doc_list = []
        for doc in documents:
            doc_list.append({
                'doc_id': doc['doc_id'],
                'filename': doc['filename'],
                'upload_date': doc['upload_date'],
                'total_chunks': doc.get('total_chunks', 0),
                'embedding_status': doc.get('embedding_status', 'unknown'),
                'file_size': doc.get('file_size', 0)
            })
        
        return cors_response(200, {
            'documents': doc_list,
            'count': len(doc_list)
        })
        
    except Exception as e:
        print(f"List documents error: {str(e)}")
        return cors_response(500, {'error': 'Internal server error'})


def handle_get_document(event):
    """
    Get specific document details
    """
    try:
        # Extract doc_id from path
        path = event.get('path', '')
        doc_id = path.split('/')[-1]
        
        table = dynamodb.Table(METADATA_TABLE)
        response = table.get_item(Key={'doc_id': doc_id})
        
        if 'Item' not in response:
            return cors_response(404, {'error': 'Document not found'})
        
        doc = response['Item']
        
        # Remove embeddings from response (too large)
        if 'chunks_with_embeddings' in doc:
            del doc['chunks_with_embeddings']
        
        return cors_response(200, doc)
        
    except Exception as e:
        print(f"Get document error: {str(e)}")
        return cors_response(500, {'error': 'Internal server error'})


def cors_response(status_code, body):
    """Helper to return CORS-enabled response"""
    return {
        'statusCode': status_code,
        'headers': CORS_HEADERS,
        'body': json.dumps(body)
    }