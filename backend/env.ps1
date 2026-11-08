Set-Location $PSScriptRoot

$env:POSTGRES_HOST = 'localhost'
$env:POSTGRES_PORT = '5432'
$env:POSTGRES_DB = 'sales_recovery'
$env:POSTGRES_USER = 'postgres'
$env:POSTGRES_PASSWORD = 'jrr!sZYP!J9cag'

python -m uvicorn main:app --reload