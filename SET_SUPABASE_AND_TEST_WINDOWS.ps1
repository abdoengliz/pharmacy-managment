$ErrorActionPreference = "Stop"

Write-Host "Supabase connection test for Pharma ERP" -ForegroundColor Cyan
$password = Read-Host "Enter the Supabase database password" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $encoded = [System.Uri]::EscapeDataString($plain)
    $env:DATABASE_URL = "postgresql://postgres.udczeoltolukwxxmkkpa:$encoded@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
    python TEST_SUPABASE_CONNECTION.py
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}
