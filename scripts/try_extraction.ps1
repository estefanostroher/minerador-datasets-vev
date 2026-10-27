[CmdletBinding()]
param(
    [string]$InputJson,
    [string]$OutputDirectory,
    [switch]$BuildAnalyzer,
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'Python 3.12 local ausente. Execute uv sync --locked no projeto.'
    exit 2
}
$arguments = @((Join-Path $PSScriptRoot 'manual_stages.py'), 'extraction', '--project-root', $root)
if ($Help) { $arguments += '--help' }
else {
    if ($InputJson) { $arguments += @('--input-json', $InputJson) }
    if ($OutputDirectory) { $arguments += @('--output-directory', $OutputDirectory) }
    if ($BuildAnalyzer) { $arguments += '--build-analyzer' }
}
& $python @arguments
exit $LASTEXITCODE
