#!/bin/bash

# PDF Q&A RAG System - Deploy Script (Bash)

set -e

echo "🚀 PDF Q&A RAG System - Deployment Script"
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration
PROJECT_NAME=${1:-"pdf-qa-rag"}
AWS_REGION="us-east-1"  # Bedrock only in us-east-1
STACK_NAME="${PROJECT_NAME}-stack"

echo "📋 Configuration:"
echo "  Project Name: $PROJECT_NAME"
echo "  AWS Region: $AWS_REGION"
echo "  Stack Name: $STACK_NAME"
echo ""

# Check AWS CLI
echo "🔍 Checking prerequisites..."
if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ AWS CLI not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} AWS CLI installed"

# Check credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}❌ AWS credentials not configured${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} AWS credentials configured"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${GREEN}✓${NC} AWS Account: $AWS_ACCOUNT_ID"

echo ""
echo "⚠️  Step 1: Checking Bedrock model access..."
echo "  IMPORTANT: You must enable Bedrock models manually!"
echo "  1. Go to: https://console.aws.amazon.com/bedrock"
echo "  2. Click 'Model access' in left sidebar"
echo "  3. Enable: Titan Embeddings G1 - Text"
echo "  4. Enable: Claude 3 Sonnet"
echo ""
read -p "Have you enabled Bedrock models? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Please enable Bedrock models first, then run this script again"
    exit 0
fi

echo ""
echo "📦 Step 2: Packaging Lambda functions..."

# Package pdf-processor
echo "  Packaging pdf-processor..."
cd lambda/pdf-processor
rm -f function.zip
zip -q function.zip handler.py
cd ../..

# Package embeddings-generator
echo "  Packaging embeddings-generator..."
cd lambda/embeddings-generator
rm -f function.zip
# Include dependencies
if [ ! -d "package" ]; then
    mkdir package
    pip install -r requirements.txt -t package -q
fi
cp handler.py package/
cd package
zip -qr ../function.zip .
cd ..
rm -rf package
cd ../..

# Package qa-api
echo "  Packaging qa-api..."
cd lambda/qa-api
rm -f function.zip
# Include dependencies
if [ ! -d "package" ]; then
    mkdir package
    pip install -r requirements.txt -t package -q
fi
cp handler.py package/
cd package
zip -qr ../function.zip .
cd ..
rm -rf package
cd ../..

echo -e "${GREEN}✓${NC} Lambda functions packaged"

echo ""
echo "☁️  Step 3: Creating deployment bucket..."

DEPLOYMENT_BUCKET="${PROJECT_NAME}-deploy-$(date +%s)"

if aws s3 mb "s3://$DEPLOYMENT_BUCKET" --region "$AWS_REGION" 2>&1 | grep -q 'make_bucket:'; then
    echo -e "${GREEN}✓${NC} Deployment bucket created: $DEPLOYMENT_BUCKET"
fi

echo ""
echo "📤 Step 4: Packaging CloudFormation template..."

aws cloudformation package \
    --template-file cloudformation/template.yaml \
    --s3-bucket "$DEPLOYMENT_BUCKET" \
    --output-template-file packaged-template.yaml \
    --region "$AWS_REGION" > /dev/null

echo -e "${GREEN}✓${NC} Template packaged"

echo ""
echo "🚀 Step 5: Deploying CloudFormation stack..."
echo "  This may take 3-4 minutes..."

aws cloudformation deploy \
    --template-file packaged-template.yaml \
    --stack-name "$STACK_NAME" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides ProjectName="$PROJECT_NAME" \
    --region "$AWS_REGION"

echo -e "${GREEN}✓${NC} Stack deployed"

echo ""
echo "🔄 Step 6: Updating Lambda function codes..."

# Get function names
PDF_PROCESSOR=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`PdfProcessorFunctionName`].OutputValue' \
    --output text \
    --region "$AWS_REGION")

EMBEDDINGS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`EmbeddingsFunctionName`].OutputValue' \
    --output text \
    --region "$AWS_REGION")

QA_API=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`QaApiFunctionName`].OutputValue' \
    --output text \
    --region "$AWS_REGION")

# Update functions
echo "  Updating pdf-processor..."
aws lambda update-function-code \
    --function-name "$PDF_PROCESSOR" \
    --zip-file fileb://lambda/pdf-processor/function.zip \
    --region "$AWS_REGION" > /dev/null

echo "  Updating embeddings-generator..."
aws lambda update-function-code \
    --function-name "$EMBEDDINGS" \
    --zip-file fileb://lambda/embeddings-generator/function.zip \
    --region "$AWS_REGION" > /dev/null

echo "  Updating qa-api..."
aws lambda update-function-code \
    --function-name "$QA_API" \
    --zip-file fileb://lambda/qa-api/function.zip \
    --region "$AWS_REGION" > /dev/null

echo -e "${GREEN}✓${NC} Lambda functions updated"

echo ""
echo "🔗 Step 7: Configuring S3 event notifications..."

PDF_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`PdfBucketName`].OutputValue' \
    --output text \
    --region "$AWS_REGION")

LAMBDA_ARN=$(aws lambda get-function \
    --function-name "$PDF_PROCESSOR" \
    --query 'Configuration.FunctionArn' \
    --output text \
    --region "$AWS_REGION")

# Create notification configuration
cat > /tmp/s3-notification.json << EOF
{
  "LambdaFunctionConfigurations": [
    {
      "Id": "PdfProcessor",
      "LambdaFunctionArn": "$LAMBDA_ARN",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {
        "Key": {
          "FilterRules": [
            {
              "Name": "suffix",
              "Value": ".pdf"
            }
          ]
        }
      }
    }
  ]
}
EOF

aws s3api put-bucket-notification-configuration \
    --bucket "$PDF_BUCKET" \
    --notification-configuration file:///tmp/s3-notification.json \
    --region "$AWS_REGION"

rm /tmp/s3-notification.json

echo -e "${GREEN}✓${NC} S3 event notifications configured"

echo ""
echo "📊 Step 8: Retrieving outputs..."

API_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' \
    --output text \
    --region "$AWS_REGION")

echo ""
echo "========================================="
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "========================================="
echo ""
echo "📌 Stack Outputs:"
echo ""
echo "  API Endpoint:"
echo -e "    ${GREEN}$API_ENDPOINT${NC}"
echo ""
echo "  PDF S3 Bucket:"
echo "    $PDF_BUCKET"
echo ""
echo "  Lambda Functions:"
echo "    - PDF Processor: $PDF_PROCESSOR"
echo "    - Embeddings: $EMBEDDINGS"
echo "    - Q&A API: $QA_API"
echo ""
echo "========================================="
echo ""
echo "🧪 Next Steps:"
echo ""
echo "1. Upload a PDF:"
echo "   aws s3 cp your-document.pdf s3://$PDF_BUCKET/"
echo ""
echo "2. Run dashboard:"
echo "   cd dashboard"
echo "   pip install -r requirements.txt"
echo "   streamlit run app.py"
echo ""
echo "3. Configure dashboard:"
echo -e "   API Endpoint: ${GREEN}$API_ENDPOINT${NC}"
echo "   S3 Bucket: $PDF_BUCKET"
echo ""
echo "4. Test Q&A:"
echo "   Upload PDF -> Wait 1-2 min -> Ask questions!"
echo ""
echo "========================================="
echo ""

# Save deployment info
cat > deployment-info.txt << EOF
PDF Q&A RAG System - Deployment Info
====================================

Deployed: $(date)
Stack Name: $STACK_NAME
AWS Region: $AWS_REGION
AWS Account: $AWS_ACCOUNT_ID

API Endpoint: $API_ENDPOINT
PDF Bucket: $PDF_BUCKET
PDF Processor: $PDF_PROCESSOR
Embeddings Function: $EMBEDDINGS
Q&A API Function: $QA_API

Bedrock Models Required:
- Titan Embeddings G1 - Text
- Claude 3 Sonnet

Upload Command:
aws s3 cp your-document.pdf s3://$PDF_BUCKET/

Dashboard Config:
- API Endpoint: $API_ENDPOINT
- S3 Bucket: $PDF_BUCKET
EOF

echo -e "${GREEN}✓${NC} Deployment info saved to: deployment-info.txt"
echo ""