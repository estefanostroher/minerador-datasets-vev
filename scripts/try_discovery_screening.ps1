param(
    [string]$CreatedFrom = '2024-01-01T00:00:00Z',
    [string]$CreatedUntil = '2024-01-01T00:05:00Z',
    [ValidateRange(1, 2147483647)]
    [int]$MaxRepos = 10
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot 'try_discovery_screening.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Python do ambiente .venv ausente. Use o ambiente existente do projeto.'
}
if ([string]::IsNullOrWhiteSpace($env:GITHUB_TOKEN)) {
    $secret = Read-Host 'GITHUB_TOKEN (entrada mascarada, apenas nesta sessao)' -AsSecureString
    try {
        $credential = New-Object System.Net.NetworkCredential('', $secret)
        $env:GITHUB_TOKEN = $credential.Password
    }
    finally {
        if ($null -ne $secret) { $secret.Dispose() }
        Remove-Variable secret, credential -ErrorAction SilentlyContinue
    }
}
& $python $script --project-root $projectRoot --created-from $CreatedFrom --created-until $CreatedUntil --max-repos $MaxRepos
exit $LASTEXITCODE
