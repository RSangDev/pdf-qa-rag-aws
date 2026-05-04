"""
PDF Q&A RAG System - Streamlit Dashboard
Chat interface for asking questions about uploaded PDFs
"""

import streamlit as st
import requests
import boto3
import json
from datetime import datetime

# Page config
st.set_page_config(
    page_title="PDF Q&A System",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
    <style>
    .stChatMessage {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
    }
    .source-box {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin-top: 0.5rem;
        border-left: 3px solid #1f77b4;
    }
    </style>
""", unsafe_allow_html=True)

# Session state
if 'api_endpoint' not in st.session_state:
    st.session_state.api_endpoint = ""
if 's3_bucket' not in st.session_state:
    st.session_state.s3_bucket = ""
if 'messages' not in st.session_state:
    st.session_state.messages = []
if 'selected_doc' not in st.session_state:
    st.session_state.selected_doc = None

# Sidebar Configuration
with st.sidebar:
    st.title("⚙️ Configuration")
    
    # API Endpoint
    api_endpoint = st.text_input(
        "API Gateway Endpoint",
        value=st.session_state.api_endpoint,
        placeholder="https://xxx.execute-api.region.amazonaws.com/prod",
        help="Get from CloudFormation outputs"
    )
    
    if api_endpoint != st.session_state.api_endpoint:
        st.session_state.api_endpoint = api_endpoint
    
    # S3 Bucket
    s3_bucket = st.text_input(
        "S3 Bucket Name",
        value=st.session_state.s3_bucket,
        placeholder="pdf-qa-rag-pdfs-xxxxx",
        help="Get from CloudFormation outputs"
    )
    
    if s3_bucket != st.session_state.s3_bucket:
        st.session_state.s3_bucket = s3_bucket
    
    st.divider()
    
    # Upload PDF
    st.subheader("📤 Upload PDF")
    uploaded_file = st.file_uploader(
        "Choose a PDF",
        type=['pdf'],
        help="Upload will trigger automatic processing with Textract and embedding generation"
    )
    
    if uploaded_file and st.session_state.s3_bucket:
        if st.button("🚀 Upload & Process", use_container_width=True):
            with st.spinner("Uploading to S3..."):
                try:
                    # Upload to S3
                    s3 = boto3.client('s3')
                    filename = f"uploads/{uploaded_file.name}"
                    s3.upload_fileobj(
                        uploaded_file,
                        st.session_state.s3_bucket,
                        filename
                    )
                    st.success(f"✅ Uploaded! Processing will take ~1-2 minutes")
                    st.info("⏱️ PDF is being processed with Textract and embeddings are being generated...")
                except Exception as e:
                    st.error(f"❌ Upload failed: {str(e)}")
    
    st.divider()
    
    # Document selector
    st.subheader("📚 Select Document")
    
    if st.button("🔄 Refresh Documents", use_container_width=True):
        if st.session_state.api_endpoint:
            try:
                response = requests.get(
                    f"{st.session_state.api_endpoint}/documents",
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    st.session_state.documents = data.get('documents', [])
            except:
                st.session_state.documents = []
    
    if 'documents' in st.session_state and st.session_state.documents:
        doc_options = ["All Documents"] + [
            f"{doc['filename']} ({doc['embedding_status']})"
            for doc in st.session_state.documents
        ]
        
        selected = st.selectbox(
            "Search in:",
            doc_options
        )
        
        if selected != "All Documents":
            idx = doc_options.index(selected) - 1
            st.session_state.selected_doc = st.session_state.documents[idx]['doc_id']
        else:
            st.session_state.selected_doc = None
        
        # Show document stats
        ready_docs = sum(1 for doc in st.session_state.documents if doc['embedding_status'] == 'completed')
        st.caption(f"📊 {ready_docs}/{len(st.session_state.documents)} documents ready for Q&A")

# Main content
st.title("📄 PDF Q&A System")
st.markdown("**Ask questions about your PDF documents** - Powered by AWS Bedrock & RAG")

# Check configuration
if not st.session_state.api_endpoint:
    st.warning("⚠️ Configure API endpoint in sidebar to get started")
    st.info("""
    **Quick Start:**
    1. Deploy the CloudFormation stack
    2. Enable Bedrock models (Titan Embeddings + Claude 3 Sonnet)
    3. Copy API endpoint and S3 bucket from outputs
    4. Upload PDFs and start asking questions! 🚀
    """)
    st.stop()

# Tabs
tab1, tab2, tab3 = st.tabs(["💬 Chat", "📚 Documents", "ℹ️ About"])

# Tab 1: Chat Interface
with tab1:
    # Display chat messages
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            
            # Show sources if available
            if "sources" in message and message["sources"]:
                with st.expander("📖 View Sources"):
                    for i, source in enumerate(message["sources"], 1):
                        st.markdown(f"""
                        <div class="source-box">
                            <strong>Source {i}</strong> (Relevance: {source.get('relevance_score', 'N/A')})<br>
                            <em>{source['text_preview']}</em>
                        </div>
                        """, unsafe_allow_html=True)
    
    # Chat input
    if prompt := st.chat_input("Ask a question about your documents..."):
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Generate response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    # Call API
                    payload = {
                        "question": prompt,
                        "top_k": 5
                    }
                    
                    if st.session_state.selected_doc:
                        payload["doc_id"] = st.session_state.selected_doc
                    
                    response = requests.post(
                        f"{st.session_state.api_endpoint}/ask",
                        json=payload,
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        answer = data.get('answer', 'No answer generated')
                        sources = data.get('sources', [])
                        chunks_used = data.get('chunks_used', 0)
                        confidence = data.get('confidence', 'unknown')
                        
                        # Display answer
                        st.markdown(answer)
                        
                        # Display metadata
                        st.caption(f"ℹ️ Confidence: {confidence} | Sources used: {chunks_used}")
                        
                        # Store message with sources
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": answer,
                            "sources": sources
                        })
                        
                        # Show sources
                        if sources:
                            with st.expander("📖 View Sources"):
                                for i, source in enumerate(sources, 1):
                                    st.markdown(f"""
                                    <div class="source-box">
                                        <strong>Source {i}</strong> (Relevance: {source.get('relevance_score', 'N/A')})<br>
                                        <em>{source['text_preview']}</em>
                                    </div>
                                    """, unsafe_allow_html=True)
                    else:
                        error_msg = f"API Error: {response.status_code}"
                        st.error(error_msg)
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": error_msg
                        })
                        
                except Exception as e:
                    error_msg = f"Error: {str(e)}"
                    st.error(error_msg)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": error_msg
                    })
    
    # Clear chat button
    if st.session_state.messages:
        if st.button("🗑️ Clear Chat History"):
            st.session_state.messages = []
            st.rerun()

# Tab 2: Documents
with tab2:
    st.subheader("📚 Uploaded Documents")
    
    if st.button("🔄 Refresh List", key="refresh_docs_tab"):
        try:
            response = requests.get(
                f"{st.session_state.api_endpoint}/documents",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                st.session_state.documents = data.get('documents', [])
        except Exception as e:
            st.error(f"Error loading documents: {str(e)}")
    
    if 'documents' in st.session_state and st.session_state.documents:
        for doc in st.session_state.documents:
            with st.expander(f"📄 {doc['filename']}"):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.write(f"**Document ID:** `{doc['doc_id'][:16]}...`")
                    st.write(f"**Upload Date:** {doc['upload_date'][:10]}")
                    st.write(f"**File Size:** {doc['file_size']:,} bytes")
                
                with col2:
                    st.write(f"**Total Chunks:** {doc['total_chunks']}")
                    
                    status = doc['embedding_status']
                    if status == 'completed':
                        st.success(f"**Status:** ✅ {status}")
                    elif status == 'pending':
                        st.warning(f"**Status:** ⏳ {status}")
                    else:
                        st.info(f"**Status:** {status}")
    else:
        st.info("No documents uploaded yet. Use the sidebar to upload PDFs!")

# Tab 3: About
with tab3:
    st.subheader("ℹ️ About This System")
    
    st.markdown("""
    ### 🎯 What is this?
    
    A **RAG (Retrieval Augmented Generation)** system that lets you ask questions about PDF documents.
    
    ### 🏗️ How it works
    
    1. **Upload PDF** → Stored in S3
    2. **Extract Text** → AWS Textract OCR
    3. **Generate Embeddings** → Bedrock Titan (vector embeddings)
    4. **Store Vectors** → DynamoDB (with fallback from OpenSearch)
    5. **Ask Question** → Generates query embedding
    6. **Search** → Finds relevant document chunks (cosine similarity)
    7. **Generate Answer** → Bedrock Claude 3 Sonnet with context
    
    ### 🤖 AI Models Used
    
    - **Titan Embeddings** - Convert text to vector embeddings (1536 dimensions)
    - **Claude 3 Sonnet** - Generate natural language answers
    - **Textract** - Extract text from PDFs (OCR)
    
    ### 🛠️ AWS Services
    
    - S3 - PDF storage
    - Lambda - Processing pipeline (3 functions)
    - Textract - Text extraction
    - Bedrock - AI/ML models
    - DynamoDB - Metadata + embeddings storage
    - API Gateway - REST API
    
    ### 💰 Cost
    
    **~$1-5/month** with moderate usage:
    - Textract: $1.50 per 1,000 pages
    - Bedrock Titan: $0.0001 per 1k tokens
    - Bedrock Claude: $0.003 per 1k input tokens
    - Lambda, DynamoDB, S3: Free Tier
    
    ### 📚 Use Cases
    
    - Legal document analysis
    - Research paper Q&A
    - Technical documentation search
    - Contract review
    - Medical records analysis
    - Knowledge base queries
    """)

# Footer
st.divider()
st.markdown("""
    <div style='text-align: center; color: gray; padding: 20px;'>
        <p>🚀 PDF Q&A RAG System • Powered by AWS Bedrock</p>
        <p style='font-size: 0.9em;'>Textract + Titan Embeddings + Claude 3 Sonnet</p>
    </div>
""", unsafe_allow_html=True)