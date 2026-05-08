"""
PDF Processor - Extract text from PDFs using AWS Textract
Triggered by S3 upload, chunks text, prepares for embedding
"""

import json
import boto3
import os
from datetime import datetime
from urllib.parse import unquote_plus
import uuid
import re

# AWS Clients
s3 = boto3.client('s3')
textract = boto3.client('textract')
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Environment variables
METADATA_TABLE = os.environ.get('METADATA_TABLE', 'pdf-metadata')
EMBEDDINGS_FUNCTION = os.environ.get('EMBEDDINGS_FUNCTION', '')

# Configuration
CHUNK_SIZE = 500  # Characters per chunk
CHUNK_OVERLAP = 50  # Overlap between chunks


def lambda_handler(event, context):
    """
    Triggered by S3 upload event
    Extracts text from PDF and prepares chunks for embedding
    """
    print(f"Event: {json.dumps(event)}")
    
    try:
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key = unquote_plus(record['s3']['object']['key'])
            
            print(f"Processing PDF: s3://{bucket}/{key}")
            
            # Skip if not a PDF
            if not key.lower().endswith('.pdf'):
                print(f"Skipping non-PDF file: {key}")
                continue
            
            # Generate unique document ID
            doc_id = str(uuid.uuid4())
            
            # Extract text from PDF
            text = extract_text_from_pdf(bucket, key)
            
            if not text or len(text.strip()) < 50:
                print(f"⚠️ No text extracted from {key}")
                continue
            
            # Split into chunks
            chunks = create_chunks(text, CHUNK_SIZE, CHUNK_OVERLAP)
            
            print(f"✓ Extracted {len(text)} characters, {len(chunks)} chunks")
            
            # Save metadata to DynamoDB
            save_metadata(doc_id, bucket, key, text, chunks)
            
            # Trigger embeddings generation asynchronously
            trigger_embeddings_generation(doc_id, chunks)
            
            print(f"✓ PDF processed successfully: {doc_id}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'PDFs processed successfully'})
        }
        
    except Exception as e:
        print(f"Error processing PDF: {str(e)}")
        raise


def extract_text_from_pdf(bucket, key):
    """
    Extract text from PDF using pypdf library (no Textract needed)
    """
    try:
        print(f"Extracting text from {key} using pypdf...")
        
        # Get PDF bytes from S3
        response = s3.get_object(Bucket=bucket, Key=key)
        pdf_bytes = response['Body'].read()
        
        # Import pypdf
        import io
        from pypdf import PdfReader
        
        # Create PDF reader
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PdfReader(pdf_file)
        
        # Extract text from all pages
        text_blocks = []
        
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text()
            if text.strip():
                text_blocks.append(text)
                print(f"  Page {page_num + 1}: {len(text)} chars")
        
        # Combine all text
        full_text = '\n\n'.join(text_blocks)
        
        print(f"✓ Extracted {len(full_text)} characters from {len(reader.pages)} pages")
        
        if len(full_text.strip()) < 50:
            raise Exception("PDF appears to be empty or scanned image (no extractable text)")
        
        return full_text
        
    except Exception as e:
        print(f"PDF extraction error: {str(e)}")
        raise Exception(f"Failed to extract text from PDF: {str(e)}")


def create_chunks(text, chunk_size=500, overlap=50):
    """
    Split text into overlapping chunks for better context
    """
    # Clean text
    text = re.sub(r'\s+', ' ', text).strip()
    
    chunks = []
    start = 0
    
    while start < len(text):
        # Get chunk
        end = start + chunk_size
        chunk = text[start:end]
        
        # Try to break at sentence boundary
        if end < len(text):
            # Look for sentence end
            last_period = chunk.rfind('.')
            last_newline = chunk.rfind('\n')
            break_point = max(last_period, last_newline)
            
            if break_point > chunk_size // 2:  # Only if break point is reasonable
                chunk = chunk[:break_point + 1]
                end = start + break_point + 1
        
        chunks.append({
            'chunk_id': len(chunks),
            'text': chunk.strip(),
            'start_char': start,
            'end_char': end
        })
        
        # Move start with overlap
        start = end - overlap
    
    return chunks


def save_metadata(doc_id, bucket, key, full_text, chunks):
    """
    Save document metadata to DynamoDB
    """
    try:
        table = dynamodb.Table(METADATA_TABLE)
        
        # Extract filename
        filename = key.split('/')[-1]
        
        # Create metadata item
        item = {
            'doc_id': doc_id,
            'filename': filename,
            'bucket': bucket,
            's3_key': key,
            'upload_date': datetime.utcnow().isoformat(),
            'file_size': get_file_size(bucket, key),
            'total_characters': len(full_text),
            'total_chunks': len(chunks),
            'chunks': chunks,  # Store chunks with metadata
            'processing_status': 'text_extracted',
            'embedding_status': 'pending'
        }
        
        table.put_item(Item=item)
        
        print(f"✓ Metadata saved for {doc_id}")
        
    except Exception as e:
        print(f"Failed to save metadata: {str(e)}")
        raise


def trigger_embeddings_generation(doc_id, chunks):
    """
    Trigger embeddings Lambda function asynchronously
    """
    try:
        if not EMBEDDINGS_FUNCTION:
            print("⚠️ Embeddings function not configured")
            return
        
        # Invoke embeddings function asynchronously
        payload = {
            'doc_id': doc_id,
            'chunks': chunks
        }
        
        lambda_client.invoke(
            FunctionName=EMBEDDINGS_FUNCTION,
            InvocationType='Event',  # Async
            Payload=json.dumps(payload)
        )
        
        print(f"✓ Triggered embeddings generation for {doc_id}")
        
    except Exception as e:
        print(f"Failed to trigger embeddings: {str(e)}")


def get_file_size(bucket, key):
    """Get file size from S3"""
    try:
        response = s3.head_object(Bucket=bucket, Key=key)
        return response['ContentLength']
    except:
        return 0