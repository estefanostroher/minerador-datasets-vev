[CmdletBinding()]
param(
    [string]$CreatedFrom = '2024-01-01T00:00:00Z',
    [string]$CreatedUntil = '2024-01-01T00:05:00Z',
    [ValidateRange(1, 2147483647)][int]$MaxRepos = 10,
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
$arguments = @((Join-Path $PSScriptRoot 'manual_stages.py'), 'all', '--project-root', $root)
if ($Help) { $arguments += '--help' }
else {
    $arguments += @('--created-from', $CreatedFrom, '--created-until', $CreatedUntil,
        '--max-repos', [string]$MaxRepos)
    if ($OutputDirectory) { $arguments += @('--output-directory', $OutputDirectory) }
    if ($BuildAnalyzer) { $arguments += '--build-analyzer' }
}
& $python @arguments
exit $LASTEXITCODE
