$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Pharma ERP - SQLite to Supabase PostgreSQL migration" -ForegroundColor Cyan
Write-Host "A backup of the local SQLite file will be created first." -ForegroundColor Yellow

if (-not (Test-Path ".\pharmacy_finance.db")) {
    throw "pharmacy_finance.db was not found in this folder."
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $PSScriptRoot "backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item ".\pharmacy_finance.db" (Join-Path $backupDir "pre_supabase_migration_$stamp.db") -Force

$secure = Read-Host "Enter the Supabase database password" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
if ([string]::IsNullOrWhiteSpace($password)) { throw "Password cannot be empty." }

$encoded = [Uri]::EscapeDataString($password)
$env:DATABASE_URL = "postgresql://postgres.udczeoltolukwxxmkkpa:$encoded@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"

Write-Host "`nFirst, running a local analysis..." -ForegroundColor Cyan
python .\MIGRATE_SQLITE_TO_SUPABASE.py --dry-run --report .\SUPABASE_MIGRATION_DRY_RUN.json
if ($LASTEXITCODE -ne 0) { throw "Dry-run failed." }

Write-Host "`nWARNING: The migration can replace existing ERP tables in Supabase." -ForegroundColor Yellow
$answer = Read-Host "Type MIGRATE to continue"
if ($answer -cne "MIGRATE") {
    Write-Host "Cancelled. Nothing was changed in Supabase." -ForegroundColor Yellow
    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
    exit 0
}

python .\MIGRATE_SQLITE_TO_SUPABASE.py --reset --report .\SUPABASE_MIGRATION_REPORT.json
$exitCode = $LASTEXITCODE
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
if ($exitCode -ne 0) { throw "Migration failed. See SUPABASE_MIGRATION_REPORT.json" }

Write-Host "`nSUCCESS: Database migration completed." -ForegroundColor Green
Write-Host "Next step: run the application in PostgreSQL mode and perform functional tests." -ForegroundColor Green
