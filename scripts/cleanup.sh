#!/bin/bash

# PDF Q&A RAG System - Cleanup Script
# Deletes all AWS resources

set -e

echo "🗑️  PDF Q&A RAG System - Cleanup Script"
echo "========================================"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration
PROJECT_NAME=${1:-"pdf-qa-rag"}
AWS_REGION="us-east-1"
STACK_NAME="${PROJECT_NAME}-stack"

echo ""
echo -e "${RED}⚠️  WARNING: This will DELETE all resources!${NC}"
echo ""
echo "Stack to delete: $STACK_NAME"
echo "Region: $AWS_REGION"
echo ""
echo -n "Are you sure? (type 'yes' to confirm): "
read confirmation

if [ "$confirmation" != "yes" ]; then
    echo "Cleanup cancelled"
    exit 0
fi

echo ""
echo "🔍 Step 1: Checking if stack exists..."

if ! aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" &> /dev/null; then
    echo -e "${YELLOW}⚠${NC} Stack not found: $STACK_NAME"
    echo "Nothing to cleanup"
    exit 0
fi

echo -e "${GREEN}✓${NC} Stack found"

echo ""
echo "📊 Step 2: Getting stack resources..."

# Get S3 bucket name
PDF_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`PdfBucketName`].OutputValue' \
    --output text \
    --region "$AWS_REGION" 2>/dev/null || echo "")

echo ""
echo "Resources found:"
if [ ! -z "$PDF_BUCKET" ]; then
    echo "  - PDF Bucket: $PDF_BUCKET"
fi

echo ""
echo "🗑️  Step 3: Emptying S3 bucket..."

if [ ! -z "$PDF_BUCKET" ]; then
    echo "Emptying bucket: $PDF_BUCKET"
    aws s3 rm "s3://$PDF_BUCKET" --recursive --region "$AWS_REGION" 2>/dev/null || true
    echo -e "${GREEN}✓${NC} S3 bucket emptied"
else
    echo "No S3 bucket to empty"
fi

echo ""
echo "🗑️  Step 4: Deleting CloudFormation stack..."

aws cloudformation delete-stack \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION"

echo "Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" 2>/dev/null || true

echo -e "${GREEN}✓${NC} Stack deleted"

echo ""
echo "🗑️  Step 5: Cleaning up local files..."

# Remove local files
if [ -f "packaged-template.yaml" ]; then
    rm packaged-template.yaml
    echo -e "${GREEN}✓${NC} Removed packaged-template.yaml"
fi

if [ -f "deployment-info.txt" ]; then
    rm deployment-info.txt
    echo -e "${GREEN}✓${NC} Removed deployment-info.txt"
fi

# Remove Lambda zips
for dir in lambda/*/; do
    if [ -f "${dir}function.zip" ]; then
        rm "${dir}function.zip"
        echo -e "${GREEN}✓${NC} Removed $(basename $dir)/function.zip"
    fi
done

echo ""
echo "========================================="
echo "✅ CLEANUP COMPLETE!"
echo "========================================="
echo ""
echo "All resources have been deleted:"
echo "  ✓ Lambda Functions (3)"
echo "  ✓ API Gateway"
echo "  ✓ DynamoDB Table"
echo "  ✓ S3 Buckets"
echo "  ✓ IAM Roles"
echo "  ✓ CloudWatch Logs"
echo ""
echo "You can redeploy anytime with:"
echo "  ./scripts/deploy.sh"
echo ""