param([int]$Port=8190,[int]$ChatPort=8191,[string]$McpProject='',[switch]$AllowMcpEdits)
$ErrorActionPreference='Stop'
$checkout=Split-Path -Parent $PSScriptRoot
$isolated=Join-Path $checkout '.acceptance'
if ($Port -lt 1024 -or $Port -gt 65535 -or $ChatPort -lt 1024 -or $ChatPort -gt 65535) { throw 'Invalid loopback port.' }
if (-not (Test-Path -LiteralPath (Join-Path $isolated 'ComfyUI/main.py'))) { throw 'Prepare the isolated ComfyUI checkout first; see acceptance documentation.' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw 'Port already occupied; do not start another editor instance.' }
function Import-PrivateKey([string]$Provider,[string]$Variable) {
    $file=Join-Path $isolated ('private/'+$Provider+'.dpapi')
    if (-not (Test-Path -LiteralPath $file)) { return }
    $secure=Get-Content -LiteralPath $file -Raw | ConvertTo-SecureString
    $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { [Environment]::SetEnvironmentVariable($Variable,[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer),'Process') }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer);$secure.Dispose() }
}
Import-PrivateKey 'ark' 'ARK_API_KEY'
Import-PrivateKey 'minimax' 'MINIMAX_API_KEY'
$config=Join-Path $isolated 'private/providers.json'
if (Test-Path -LiteralPath $config) { $env:H3_PROVIDER_CONFIG_PATH=$config }
$env:H3_ASR_MODEL=Join-Path $isolated 'asr-base'
if (-not (Test-Path -LiteralPath $env:H3_ASR_MODEL)) { $env:H3_ASR_MODEL=Join-Path $isolated 'asr-tiny' }
$env:H3_ASR_PYTHON=Join-Path $isolated 'asr-python/python.exe'
$env:H3_CHAT_PORT=[string]$ChatPort
$env:H3_CHAT_MODEL='h3-editing-intent'
if ($McpProject) {
    if ($McpProject -match '[\\/:,]' -or -not (Test-Path -LiteralPath (Join-Path $isolated ('ComfyUI/output/h3take_projects/'+$McpProject+'/project.json')))) { throw 'Choose one existing isolated project folder.' }
    $privateFile=Join-Path $isolated 'mcp-private.json'
    if (-not (Test-Path -LiteralPath $privateFile)) { throw 'Create private MCP tokens as documented first.' }
    $credentials=Get-Content -LiteralPath $privateFile -Raw | ConvertFrom-Json
    $env:H3_MCP_TOKEN=$credentials.write;$env:H3_MCP_READ_TOKEN=$credentials.read
    $env:H3_MCP_PROJECTS=$McpProject;$env:H3_MCP_ALLOW_EDITS=if ($AllowMcpEdits) {'1'} else {'0'}
} else { $env:H3_MCP_PROJECTS='';$env:H3_MCP_ALLOW_EDITS='0' }
$previousLocation=Get-Location
try {
    Set-Location -LiteralPath (Join-Path $isolated 'ComfyUI')
    & '../python/python.exe' -B -s main.py --disable-auto-launch --listen 127.0.0.1 --port $Port --cuda-device 1 --extra-model-paths-config '../extra_model_paths.yaml' *> '../startup.log'
} finally {
    Set-Location -LiteralPath $previousLocation
    foreach ($variable in 'ARK_API_KEY','MINIMAX_API_KEY','H3_MCP_TOKEN','H3_MCP_READ_TOKEN') { [Environment]::SetEnvironmentVariable($variable,$null,'Process') }
}
