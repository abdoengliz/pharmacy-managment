$ErrorActionPreference = "Stop"
Write-Host "Pharma ERP - Supabase PostgreSQL pilot" -ForegroundColor Cyan
$passwordSecure = Read-Host "Enter Supabase database password" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($passwordSecure)
try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
$encoded = [System.Uri]::EscapeDataString($password)
$env:DATABASE_URL = "postgresql://postgres.udczeoltolukwxxmkkpa:$encoded@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
$env:APP_ENV = "development"
$env:SESSION_COOKIE_SECURE = "false"
if (-not $env:SECRET_KEY) { $env:SECRET_KEY = "supabase-pilot-local-secret-key-change-before-production-2026" }
python -m pip install -r requirements.txt
python TEST_SUPABASE_CONNECTION.py
Write-Host "Starting application at http://127.0.0.1:5000" -ForegroundColor Green
python app.py
