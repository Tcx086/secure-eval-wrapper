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

function Invoke-PsqlScalar {
    param([string]$Sql)

    $psql = Get-LocalPsql
    if ($null -ne $psql) {
        $env:PGPASSWORD = $env:POSTGRES_PASSWORD
        $output = & $psql.Source `
            "--host=$($env:POSTGRES_HOST)" `
            "--port=$($env:POSTGRES_PORT)" `
            "--username=$($env:POSTGRES_USER)" `
            "--dbname=$($env:POSTGRES_DB)" `
            "--set=ON_ERROR_STOP=1" `
            "--no-align" `
            "--tuples-only" `
            "--command=$Sql"
        Assert-LastExitCode "psql scalar query"
        $first = $output | Select-Object -First 1
        if ($null -eq $first) { return "" }
        return $first.Trim()
    }

    $output = & docker exec -i $ContainerName psql `
        "--username=$($env:POSTGRES_USER)" `
        "--dbname=$($env:POSTGRES_DB)" `
        "--set=ON_ERROR_STOP=1" `
        "--no-align" `
        "--tuples-only" `
        "--command=$Sql"
    Assert-LastExitCode "docker psql scalar query"
    $first = $output | Select-Object -First 1
    if ($null -eq $first) { return "" }
    return $first.Trim()
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

function Get-MigrationSha256 {
    param([System.IO.FileInfo]$Migration)

    $source = [System.IO.File]::ReadAllBytes($Migration.FullName)
    $canonical = New-Object System.Collections.Generic.List[byte]
    for ($index = 0; $index -lt $source.Length; $index++) {
        if ($source[$index] -eq 13 -and $index + 1 -lt $source.Length -and $source[$index + 1] -eq 10) {
            $canonical.Add(10)
            $index++
        } else {
            $canonical.Add($source[$index])
        }
    }
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $algorithm.ComputeHash($canonical.ToArray())
    } finally {
        $algorithm.Dispose()
    }
    ([System.BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
}

function Initialize-MigrationMetadataTable {
    $sql = @"
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.schema_migrations (
    migration_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_schema_migrations_sha256
    ON audit.schema_migrations (sha256);

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON audit.schema_migrations (applied_at_utc);
"@
    Invoke-PsqlCommand $sql
}

function Get-RecordedMigrationSha256 {
    param([System.IO.FileInfo]$Migration)

    $migrationId = Escape-SqlLiteral $Migration.BaseName
    $sql = "SELECT sha256 FROM audit.schema_migrations WHERE migration_id = '$migrationId';"
    Invoke-PsqlScalar $sql
}

function Record-MigrationMetadata {
    param(
        [System.IO.FileInfo]$Migration,
        [string]$Sha256
    )

    $migrationId = Escape-SqlLiteral $Migration.BaseName
    $filename = Escape-SqlLiteral $Migration.Name
    $description = Escape-SqlLiteral (Get-MigrationDescription $Migration)

    $sql = @"
INSERT INTO audit.schema_migrations (migration_id, filename, sha256, description)
VALUES ('$migrationId', '$filename', '$Sha256', '$description');
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

function Invoke-Migrations {
    $migrations = Get-MigrationFiles
    Initialize-MigrationMetadataTable

    foreach ($migration in $migrations) {
        $sha256 = Get-MigrationSha256 $migration
        $recordedSha256 = Get-RecordedMigrationSha256 $migration

        if (-not [string]::IsNullOrWhiteSpace($recordedSha256)) {
            if ($recordedSha256 -ne $sha256) {
                throw "Recorded migration hash mismatch for $($migration.Name). Recorded $recordedSha256 but local file is $sha256."
            }
            Write-Host "Skipping $($migration.Name); metadata already records matching SHA256."
            continue
        }

        Write-Host "Applying $($migration.Name)"
        Invoke-PsqlFile $migration.FullName

        Write-Host "Recording $($migration.Name) metadata"
        Record-MigrationMetadata $migration $sha256
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
        Assert-PostgresEnv
        Invoke-Migrations
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
