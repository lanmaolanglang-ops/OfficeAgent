$ErrorActionPreference = "Stop"

$baselinePath = Join-Path (Split-Path $PSScriptRoot -Parent) "audit-warning-baseline.json"
if (-not (Test-Path -LiteralPath $baselinePath)) {
    throw "Cargo audit warning baseline is missing: $baselinePath"
}

$rawAudit = & cargo audit --json
if (-not $rawAudit) {
    throw "cargo audit did not return JSON"
}

try {
    $audit = $rawAudit | ConvertFrom-Json -AsHashTable
    $baseline = Get-Content -Raw -LiteralPath $baselinePath | ConvertFrom-Json -AsHashTable
} catch {
    throw "Unable to parse cargo audit data: $($_.Exception.Message)"
}

if ([int]$audit["vulnerabilities"]["count"] -gt 0) {
    $ids = @($audit["vulnerabilities"]["list"] | ForEach-Object { $_["advisory"]["id"] })
    throw "Cargo audit found vulnerabilities: $($ids -join ', ')"
}

$allowed = @($baseline["warnings"] | ForEach-Object { $_["id"] })
$current = @(
    $audit["warnings"].GetEnumerator() |
        ForEach-Object { $_.Value } |
        ForEach-Object { $_["advisory"]["id"] } |
        Sort-Object -Unique
)
$unexpected = @($current | Where-Object { $_ -notin $allowed })
$resolved = @($allowed | Where-Object { $_ -notin $current })

if ($unexpected.Count -gt 0) {
    throw "Cargo audit found warning IDs outside the reviewed baseline: $($unexpected -join ', ')"
}

if ($resolved.Count -gt 0) {
    Write-Warning "Remove resolved warning IDs from the Cargo audit baseline: $($resolved -join ', ')"
}

Write-Host "Cargo audit passed: 0 vulnerabilities; $($current.Count) reviewed warning IDs; 0 new warnings."
