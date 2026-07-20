[CmdletBinding()]
param(
    [string]$EnvFile
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $PSCommandPath
$RootDir = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir ".."))
$ExampleFile = Join-Path $RootDir "server\.env.example"
$HistoryDir = Join-Path $RootDir "server\history"

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $RootDir "server\.env"
} else {
    $EnvFile = [System.IO.Path]::GetFullPath($EnvFile)
}

function New-StrongSecret {
    $bytes = New-Object byte[] 48
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }

    $encoded = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")

    # neo4j-admin parses a password beginning with "-" as an option.
    return "y$($encoded.Substring(0, 63))"
}

function Get-EnvironmentValue([string]$Key) {
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$Key=", [System.StringComparison]::Ordinal)) {
            return $line.Substring($Key.Length + 1)
        }
    }
    return $null
}

function Set-EnvironmentValue([string]$Key, [string]$Value) {
    $updated = New-Object "System.Collections.Generic.List[string]"
    $replaced = $false

    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$Key=", [System.StringComparison]::Ordinal)) {
            if (-not $replaced) {
                $updated.Add("$Key=$Value")
                $replaced = $true
            }
        } else {
            $updated.Add($line)
        }
    }

    if (-not $replaced) {
        $updated.Add("$Key=$Value")
    }

    $temporaryFile = "$EnvFile.tmp.$PID.$([Guid]::NewGuid().ToString('N'))"
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllLines($temporaryFile, $updated, $utf8WithoutBom)
        Move-Item -LiteralPath $temporaryFile -Destination $EnvFile -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryFile) {
            Remove-Item -LiteralPath $temporaryFile -Force
        }
    }
}

function Set-MissingSecret([string]$Key) {
    $currentValue = Get-EnvironmentValue $Key
    $normalized = if ($null -eq $currentValue) { "" } else { $currentValue.Trim() }
    if ($normalized -and $normalized -ne '""' -and $normalized -ne "''") {
        return $false
    }

    Set-EnvironmentValue $Key (New-StrongSecret)
    return $true
}

if (-not (Test-Path -LiteralPath $ExampleFile -PathType Leaf)) {
    throw "Environment template not found: $ExampleFile"
}
if ((Test-Path -LiteralPath $EnvFile) -and -not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "$EnvFile exists but is not a regular file."
}

$createdEnvironment = $false
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Copy-Item -LiteralPath $ExampleFile -Destination $EnvFile
    $createdEnvironment = $true
}

$generatedKeys = New-Object "System.Collections.Generic.List[string]"
foreach ($key in @("POSTGRES_PASSWORD", "NEO4J_PASSWORD", "JWT_SECRET")) {
    if (Set-MissingSecret $key) {
        $generatedKeys.Add($key)
    }
}

$neo4jPassword = (Get-EnvironmentValue "NEO4J_PASSWORD").Trim()
if ($neo4jPassword.StartsWith('"') -or $neo4jPassword.StartsWith("'")) {
    $neo4jPassword = $neo4jPassword.Substring(1)
}
if ($neo4jPassword.StartsWith("-", [System.StringComparison]::Ordinal)) {
    throw "NEO4J_PASSWORD must not start with '-'; Neo4j interprets it as a command option."
}
New-Item -ItemType Directory -Path $HistoryDir -Force | Out-Null

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed or is not on PATH."
}
& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is required (run: docker compose version)."
}

Push-Location (Join-Path $RootDir "server")
try {
    & docker compose --env-file $EnvFile config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose configuration validation failed."
    }
} finally {
    Pop-Location
}

if ($createdEnvironment) {
    Write-Host "Created $EnvFile."
} else {
    Write-Host "Kept existing $EnvFile."
}
if ($generatedKeys.Count -gt 0) {
    Write-Host "Generated missing secrets: $($generatedKeys -join ', ')."
} else {
    Write-Host "Required secrets were already configured."
}
Write-Host "Docker Compose configuration is valid."
Write-Host ""
Write-Host "Next:"
Write-Host "  Set-Location `"$RootDir\server`""
Write-Host "  docker compose up -d"
