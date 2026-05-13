# Update Embeddings Generator Lambda

$ErrorActionPreference = "Stop"

Write-Host "Updating Embeddings Generator Lambda..." -ForegroundColor Green
Write-Host ""

$Region = "us-east-1"
$FunctionName = "pdf-qa-rag-embeddings"

Write-Host "Step 1: Packaging Lambda..." -ForegroundColor Yellow
Set-Location lambda/embeddings-generator

# Clean
if (Test-Path "package") { Remove-Item -Recurse -Force package }
if (Test-Path "function.zip") { Remove-Item function.zip }

# Create package
New-Item -ItemType Directory -Path package | Out-Null

# Install dependencies
Write-Host "  Installing dependencies..." -ForegroundColor Gray
pip install -r requirements.txt --target package --no-user --quiet

# Copy handler
Copy-Item handler.py package/

# Create zip
Set-Location package
Compress-Archive -Path * -DestinationPath ../function.zip -Force
Set-Location ..

# Cleanup
Remove-Item -Recurse -Force package

Set-Location ../..

Write-Host ""
Write-Host "Step 2: Updating Lambda function..." -ForegroundColor Yellow

aws lambda update-function-code `
    --function-name $FunctionName `
    --zip-file fileb://lambda/embeddings-generator/function.zip `
    --region $Region

Write-Host ""
Write-Host "SUCCESS! Lambda updated" -ForegroundColor Green
Write-Host ""
Write-Host "Now re-upload a PDF to test:" -ForegroundColor Cyan
Write-Host "  aws s3 rm s3://pdf-qa-rag-pdfs-211854352436/test.pdf" -ForegroundColor Gray
Write-Host "  aws s3 cp test.pdf s3://pdf-qa-rag-pdfs-211854352436/" -ForegroundColor Gray
Write-Host ""