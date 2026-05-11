# Update Q&A API Lambda with dependencies

$ErrorActionPreference = "Stop"

Write-Host "Updating Q&A API Lambda..." -ForegroundColor Green
Write-Host ""

$Region = "us-east-1"
$FunctionName = "pdf-qa-rag-qa-api"

Write-Host "Step 1: Installing dependencies..." -ForegroundColor Yellow
Set-Location lambda/qa-api

# Clean previous builds
if (Test-Path "package") { Remove-Item -Recurse -Force package }
if (Test-Path "function.zip") { Remove-Item function.zip }

# Install dependencies
Write-Host "  Installing opensearch-py and requests-aws4auth..." -ForegroundColor Gray
pip install -r requirements.txt --target package --no-user --quiet

# Copy handler
Copy-Item handler.py package/

# Create zip
Write-Host "  Creating deployment package..." -ForegroundColor Gray
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
    --zip-file fileb://lambda/qa-api/function.zip `
    --region $Region

Write-Host ""
Write-Host "SUCCESS! Lambda updated" -ForegroundColor Green
Write-Host ""
Write-Host "Test API:" -ForegroundColor Cyan
Write-Host "  curl https://iwh7ub9u6d.execute-api.us-east-1.amazonaws.com/prod/documents" -ForegroundColor Gray
Write-Host ""