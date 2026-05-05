# PDF Q&A RAG System - Deploy Script (PowerShell)

$ErrorActionPreference = "Stop"

Write-Host "PDF Q&A RAG System - Deployment Script" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""

# Configuration
$ProjectName = "pdf-qa-rag"
$Region = "us-east-1"  # Bedrock only available in us-east-1
$StackName = "$ProjectName-stack"

Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "  Project Name: $ProjectName"
Write-Host "  AWS Region: $Region"
Write-Host "  Stack Name: $StackName"
Write-Host ""

# Check AWS CLI
Write-Host "Checking AWS CLI..." -ForegroundColor Yellow
try {
    $awsVersion = aws --version 2>&1
    Write-Host "[OK] AWS CLI installed" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] AWS CLI not found" -ForegroundColor Red
    exit 1
}

# Check credentials
Write-Host "Checking AWS credentials..." -ForegroundColor Yellow
try {
    $identity = aws sts get-caller-identity 2>&1 | ConvertFrom-Json
    Write-Host "[OK] AWS credentials configured" -ForegroundColor Green
    Write-Host "  Account: $($identity.Account)" -ForegroundColor Gray
} catch {
    Write-Host "[ERROR] AWS credentials not configured" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Step 1: Checking Bedrock model access..." -ForegroundColor Yellow
Write-Host "  IMPORTANT: You must enable Bedrock models manually!" -ForegroundColor Yellow
Write-Host "  1. Go to: https://console.aws.amazon.com/bedrock" -ForegroundColor Gray
Write-Host "  2. Click 'Model access' in left sidebar" -ForegroundColor Gray
Write-Host "  3. Enable: Titan Embeddings G1 - Text" -ForegroundColor Gray
Write-Host "  4. Enable: Claude 3 Sonnet" -ForegroundColor Gray
Write-Host ""
$confirm = Read-Host "Have you enabled Bedrock models? (yes/no)"
if ($confirm -ne "yes") {
    Write-Host "Please enable Bedrock models first, then run this script again" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "Step 2: Packaging Lambda functions..." -ForegroundColor Yellow

# Package pdf-processor
Write-Host "  Packaging pdf-processor..." -ForegroundColor Gray
Set-Location lambda/pdf-processor
if (Test-Path "function.zip") { Remove-Item "function.zip" }
Compress-Archive -Path handler.py -DestinationPath function.zip -Force
Set-Location ../..

# Package embeddings-generator
Write-Host "  Packaging embeddings-generator..." -ForegroundColor Gray
Set-Location lambda/embeddings-generator
if (Test-Path "function.zip") { Remove-Item "function.zip" }
# Include dependencies
if (-not (Test-Path "package")) {
    New-Item -ItemType Directory -Path "package" | Out-Null
    pip install -r requirements.txt -t package --quiet
}
Copy-Item handler.py package/
Set-Location package
Compress-Archive -Path * -DestinationPath ../function.zip -Force
Set-Location ..
Remove-Item -Recurse -Force package
Set-Location ../..

# Package qa-api
Write-Host "  Packaging qa-api..." -ForegroundColor Gray
Set-Location lambda/qa-api
if (Test-Path "function.zip") { Remove-Item "function.zip" }
# Include dependencies
if (-not (Test-Path "package")) {
    New-Item -ItemType Directory -Path "package" | Out-Null
    pip install -r requirements.txt -t package --quiet
}
Copy-Item handler.py package/
Set-Location package
Compress-Archive -Path * -DestinationPath ../function.zip -Force
Set-Location ..
Remove-Item -Recurse -Force package
Set-Location ../..

Write-Host "[OK] Lambda functions packaged" -ForegroundColor Green

Write-Host ""
Write-Host "Step 3: Creating deployment bucket..." -ForegroundColor Yellow

$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$bucketName = "$ProjectName-deploy-$timestamp"

try {
    aws s3 mb "s3://$bucketName" --region $Region 2>&1 | Out-Null
    Write-Host "[OK] Deployment bucket created: $bucketName" -ForegroundColor Green
} catch {
    Write-Host "Trying alternative bucket name..." -ForegroundColor Yellow
    $bucketName = "$ProjectName-deploy-alt-$timestamp"
    aws s3 mb "s3://$bucketName" --region $Region 2>&1 | Out-Null
    Write-Host "[OK] Deployment bucket created: $bucketName" -ForegroundColor Green
}

Write-Host ""
Write-Host "Step 4: Packaging CloudFormation template..." -ForegroundColor Yellow

aws cloudformation package `
    --template-file cloudformation/template.yaml `
    --s3-bucket $bucketName `
    --output-template-file packaged-template.yaml `
    --region $Region 2>&1 | Out-Null

Write-Host "[OK] Template packaged" -ForegroundColor Green

Write-Host ""
Write-Host "Step 5: Deploying CloudFormation stack..." -ForegroundColor Yellow
Write-Host "  This may take 3-4 minutes..." -ForegroundColor Gray

try {
    aws cloudformation deploy `
        --template-file packaged-template.yaml `
        --stack-name $StackName `
        --capabilities CAPABILITY_IAM `
        --parameter-overrides ProjectName=$ProjectName `
        --region $Region
    
    Write-Host "[OK] Stack deployed successfully" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Stack deployment failed" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Step 6: Updating Lambda function codes..." -ForegroundColor Yellow

# Get function names
$outputs = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query 'Stacks[0].Outputs' 2>&1 | ConvertFrom-Json

$pdfProcessor = ($outputs | Where-Object { $_.OutputKey -eq "PdfProcessorFunctionName" }).OutputValue
$embeddings = ($outputs | Where-Object { $_.OutputKey -eq "EmbeddingsFunctionName" }).OutputValue
$qaApi = ($outputs | Where-Object { $_.OutputKey -eq "QaApiFunctionName" }).OutputValue

# Update pdf-processor
Write-Host "  Updating pdf-processor..." -ForegroundColor Gray
aws lambda update-function-code `
    --function-name $pdfProcessor `
    --zip-file fileb://lambda/pdf-processor/function.zip `
    --region $Region 2>&1 | Out-Null

# Update embeddings
Write-Host "  Updating embeddings-generator..." -ForegroundColor Gray
aws lambda update-function-code `
    --function-name $embeddings `
    --zip-file fileb://lambda/embeddings-generator/function.zip `
    --region $Region 2>&1 | Out-Null

# Update qa-api
Write-Host "  Updating qa-api..." -ForegroundColor Gray
aws lambda update-function-code `
    --function-name $qaApi `
    --zip-file fileb://lambda/qa-api/function.zip `
    --region $Region 2>&1 | Out-Null

Write-Host "[OK] Lambda functions updated" -ForegroundColor Green

Write-Host ""
Write-Host "Step 7: Configuring S3 event notifications..." -ForegroundColor Yellow

$pdfBucket = ($outputs | Where-Object { $_.OutputKey -eq "PdfBucketName" }).OutputValue

# Get Lambda ARN
$lambdaArn = aws lambda get-function `
    --function-name $pdfProcessor `
    --query 'Configuration.FunctionArn' `
    --output text `
    --region $Region 2>&1

try {
    # Create notification configuration
    $notificationConfig = @{
        LambdaFunctionConfigurations = @(
            @{
                Id = "PdfProcessor"
                LambdaFunctionArn = $lambdaArn
                Events = @("s3:ObjectCreated:*")
                Filter = @{
                    Key = @{
                        FilterRules = @(
                            @{
                                Name = "suffix"
                                Value = ".pdf"
                            }
                        )
                    }
                }
            }
        )
    }
    
    # Convert to JSON and save
    $jsonContent = $notificationConfig | ConvertTo-Json -Depth 10
    $tempFile = New-TemporaryFile
    [System.IO.File]::WriteAllText($tempFile.FullName, $jsonContent, [System.Text.UTF8Encoding]::new($false))
    
    # Apply configuration
    aws s3api put-bucket-notification-configuration `
        --bucket $pdfBucket `
        --notification-configuration "file://$($tempFile.FullName)" `
        --region $Region
    
    Remove-Item $tempFile.FullName -ErrorAction SilentlyContinue
    
    Write-Host "[OK] S3 event notifications configured" -ForegroundColor Green
} catch {
    Write-Host "[WARNING] Failed to configure S3 notifications" -ForegroundColor Yellow
    Write-Host "  You can configure manually later" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Step 8: Retrieving outputs..." -ForegroundColor Yellow

$apiEndpoint = ($outputs | Where-Object { $_.OutputKey -eq "ApiEndpoint" }).OutputValue

Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host "DEPLOYMENT SUCCESSFUL!" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""
Write-Host "Stack Outputs:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  API Endpoint:" -ForegroundColor White
Write-Host "    $apiEndpoint" -ForegroundColor Yellow
Write-Host ""
Write-Host "  PDF S3 Bucket:" -ForegroundColor White
Write-Host "    $pdfBucket" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Lambda Functions:" -ForegroundColor White
Write-Host "    - PDF Processor: $pdfProcessor" -ForegroundColor Gray
Write-Host "    - Embeddings: $embeddings" -ForegroundColor Gray
Write-Host "    - Q&A API: $qaApi" -ForegroundColor Gray
Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Upload a PDF:" -ForegroundColor White
Write-Host "   aws s3 cp your-document.pdf s3://$pdfBucket/" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Run dashboard:" -ForegroundColor White
Write-Host "   cd dashboard" -ForegroundColor Gray
Write-Host "   pip install -r requirements.txt" -ForegroundColor Gray
Write-Host "   streamlit run app.py" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Configure dashboard:" -ForegroundColor White
Write-Host "   API Endpoint: $apiEndpoint" -ForegroundColor Yellow
Write-Host "   S3 Bucket: $pdfBucket" -ForegroundColor Yellow
Write-Host ""
Write-Host "4. Test Q&A:" -ForegroundColor White
Write-Host "   Upload PDF -> Wait 1-2 min -> Ask questions!" -ForegroundColor Gray
Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""

# Save deployment info
@"
PDF Q&A RAG System - Deployment Info
====================================

Deployed: $(Get-Date)
Stack Name: $StackName
AWS Region: $Region

API Endpoint: $apiEndpoint
PDF Bucket: $pdfBucket
PDF Processor: $pdfProcessor
Embeddings Function: $embeddings
Q&A API Function: $qaApi

Bedrock Models Required:
- Titan Embeddings G1 - Text
- Claude 3 Sonnet

Upload Command:
aws s3 cp your-document.pdf s3://$pdfBucket/

Dashboard Config:
- API Endpoint: $apiEndpoint
- S3 Bucket: $pdfBucket
"@ | Out-File -FilePath deployment-info.txt -Encoding UTF8

Write-Host "[OK] Deployment info saved to: deployment-info.txt" -ForegroundColor Green
Write-Host ""