[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "status", "apply", "verify")]
    [string]$Action = "verify",
    [string]$EnvFilePath
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DefaultEnvFile = Join-Path $RepoRoot ".env"
$EnvFile = if ($EnvFilePath) { (Resolve-Path -LiteralPath $EnvFilePath).Path } else { $DefaultEnvFile }
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.postgres.yml"
$MigrationDirectory = Join-Path $RepoRoot "open-core\db\migrations"
$VerifyScript = Join-Path $RepoRoot "open-core\scripts\verify_postgres_schema.py"
$ContainerName = "secure-eval-wrapper-postgres"

function Assert-EnvFile {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "Create a local .env from .env.example, or pass -EnvFilePath with an explicit env file."
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

function Assert-PostgresEnv {
    $required = @("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    $missing = @()
    foreach ($name in $required) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            $missing += $name
        }
    }
    if ($missing.Count -gt 0) {
        throw "Missing PostgreSQL environment variables: $($missing -join ', ')."
    }
}

function Assert-LastExitCode {
    param([string]$CommandName)
    if ($LASTEXITCODE -ne 0) {
        throw "$CommandName failed with exit code $LASTEXITCODE."
    }
}

function Get-LocalPsql {
    Get-Command psql -ErrorAction SilentlyContinue
}

function Invoke-PsqlFile {
    param([string]$FilePath)

    $psql = Get-LocalPsql
    if ($null -ne $psql) {
        $env:PGPASSWORD = $env:POSTGRES_PASSWORD
        & $psql.Source `
            "--host=$($env:POSTGRES_HOST)" `
            "--port=$($env:POSTGRES_PORT)" `
            "--username=$($env:POSTGRES_USER)" `
            "--dbname=$($env:POSTGRES_DB)" `
            "--set=ON_ERROR_STOP=1" `
            "--file=$FilePath"
        Assert-LastExitCode "psql migration apply"
        return
    }

    Get-Content -Raw -LiteralPath $FilePath | docker exec -i $ContainerName psql `
        "--username=$($env:POSTGRES_USER)" `
        "--dbname=$($env:POSTGRES_DB)" `
        "--set=ON_ERROR_STOP=1" `
        "--file=-"
    Assert-LastExitCode "docker psql migration apply"
}

function Invoke-PsqlCommand {
    param([string]$Sql)

    $psql = Get-LocalPsql
    if ($null -ne $psql) {
        $env:PGPASSWORD = $env:POSTGRES_PASSWORD
        & $psql.Source `
            "--host=$($env:POSTGRES_HOST)" `
            "--port=$($env:POSTGRES_PORT)" `
            "--username=$($env:POSTGRES_USER)" `
            "--dbname=$($env:POSTGRES_DB)" `
            "--set=ON_ERROR_STOP=1" `
            "--command=$Sql"
        Assert-LastExitCode "psql command"
        return
    }

    $Sql | docker exec -i $ContainerName psql `
        "--username=$($env:POSTGRES_USER)" `
        "--dbname=$($env:POSTGRES_DB)" `
        "--set=ON_ERROR_STOP=1"
    Assert-LastExitCode "docker psql command"
}

function Escape-SqlLiteral {
    param([string]$Value)
    $Value -replace "'", "''"
}

function Get-MigrationDescription {
    param([System.IO.FileInfo]$Migration)
    $description = $Migration.BaseName -replace "^\d+_", ""
    $description = $description -replace "_", " "
    if ([string]::IsNullOrWhiteSpace($description)) {
        return $Migration.BaseName
    }
    return $description
}

function Record-MigrationMetadata {
    param([System.IO.FileInfo]$Migration)

    $migrationId = Escape-SqlLiteral $Migration.BaseName
    $filename = Escape-SqlLiteral $Migration.Name
    $sha256 = (Get-FileHash -LiteralPath $Migration.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $description = Escape-SqlLiteral (Get-MigrationDescription $Migration)

    $sql = @"
INSERT INTO audit.schema_migrations (migration_id, filename, sha256, description)
VALUES ('$migrationId', '$filename', '$sha256', '$description')
ON CONFLICT (migration_id) DO NOTHING;
"@
    Invoke-PsqlCommand $sql
}

function Get-MigrationFiles {
    $migrations = @(Get-ChildItem -LiteralPath $MigrationDirectory -Filter "*.sql" -File | Sort-Object Name)
    if ($migrations.Count -eq 0) {
        throw "No SQL migrations found in $MigrationDirectory."
    }
    $migrations
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
        Assert-PostgresEnv
        $migrations = Get-MigrationFiles
        foreach ($migration in $migrations) {
            Write-Host "Applying $($migration.Name)"
            Invoke-PsqlFile $migration.FullName
        }
        foreach ($migration in $migrations) {
            Write-Host "Recording $($migration.Name) metadata"
            Record-MigrationMetadata $migration
        }
    }
    "verify" {
        Import-LocalEnv
        Assert-PostgresEnv
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw "python is required to run schema verification."
        }

        & $python.Source $VerifyScript "--docker-container=$ContainerName"
        Assert-LastExitCode "schema verification"
    }
}
