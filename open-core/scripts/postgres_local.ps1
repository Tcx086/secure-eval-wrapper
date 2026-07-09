[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "status", "apply", "verify")]
    [string]$Action = "verify"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EnvFile = Join-Path $RepoRoot ".env"
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.postgres.yml"
$MigrationFile = Join-Path $RepoRoot "open-core\db\migrations\0001_initial_schema.sql"
$VerifyScript = Join-Path $RepoRoot "open-core\scripts\verify_postgres_schema.py"

function Assert-EnvFile {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "Create a local .env from .env.example before running PostgreSQL helpers."
    }
}

function Import-LocalEnv {
    Assert-EnvFile
    Get-Content -LiteralPath $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }

        $parts = $line -split "=", 2
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

function Assert-LastExitCode {
    param([string]$CommandName)
    if ($LASTEXITCODE -ne 0) {
        throw "$CommandName failed with exit code $LASTEXITCODE."
    }
}

switch ($Action) {
    "start" {
        Assert-EnvFile
        docker compose --env-file $EnvFile -f $ComposeFile up -d
        Assert-LastExitCode "docker compose up"
    }
    "stop" {
        Assert-EnvFile
        docker compose --env-file $EnvFile -f $ComposeFile down
        Assert-LastExitCode "docker compose down"
    }
    "status" {
        Assert-EnvFile
        docker compose --env-file $EnvFile -f $ComposeFile ps
        Assert-LastExitCode "docker compose ps"
    }
    "apply" {
        Import-LocalEnv
        $psql = Get-Command psql -ErrorAction SilentlyContinue
        if ($null -eq $psql) {
            throw "psql is required to apply migrations locally."
        }

        $env:PGPASSWORD = $env:POSTGRES_PASSWORD
        & $psql.Source `
            "--host=$($env:POSTGRES_HOST)" `
            "--port=$($env:POSTGRES_PORT)" `
            "--username=$($env:POSTGRES_USER)" `
            "--dbname=$($env:POSTGRES_DB)" `
            "--set=ON_ERROR_STOP=1" `
            "--file=$MigrationFile"
        Assert-LastExitCode "psql migration apply"
    }
    "verify" {
        Import-LocalEnv
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw "python is required to run schema verification."
        }

        & $python.Source $VerifyScript
        Assert-LastExitCode "schema verification"
    }
}
