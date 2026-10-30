# CANETA - instalador para Windows (PowerShell)
#   Na pasta de release:  clique duas vezes em install.bat (ou: powershell -ExecutionPolicy Bypass -File install.ps1)
#   Pela internet:        irm https://raw.githubusercontent.com/LucaswasTaken/CANETA_LATAM/main/install.ps1 | iex
# Instala o uv (que traz o proprio Python), baixa a CANETA do GitHub, cria atalho no Menu Iniciar e abre o app.
# Opcoes: $env:CANETA_REF = "main" (ou outra tag/branch); $env:CANETA_SOURCE = pasta local do codigo.

$ErrorActionPreference = "Stop"
$repo = "https://github.com/LucaswasTaken/CANETA_LATAM"
$ref = if ($env:CANETA_REF) { $env:CANETA_REF } else { "v0.1.8" }

Write-Host ""
Write-Host "  CANETA - Central de Assistencia e Navegacao para Escrita de Textos Avancados" -ForegroundColor Green
Write-Host ""

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[1/3] Instalando o uv (gerenciador de Python da Astral)..."
    powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
} else {
    Write-Host "[1/3] uv ja instalado."
}

Write-Host "[2/3] Baixando e instalando a CANETA $ref do GitHub (1 a 3 minutos)..."
# Sempre pelo endereco, nunca pelo nome: no PyPI existe outro pacote chamado 'caneta'.
# Opcional: $env:CANETA_OCR = "1" inclui o OCR para PDFs digitalizados (+~80 MB). PDFs recentes nao precisam.
# Opcional: $env:CANETA_SEMANTIC = "1" inclui a busca por significado (+~550 MB), que melhora muito as
# sugestoes de revista. Se ela ja estava ativa, a atualizacao a mantem.
$extra = if ($env:CANETA_OCR -eq "1") { @("--with", "rapidocr-onnxruntime>=1.4.4") } else { @() }
$cfg = Join-Path $(if ($env:CANETA_HOME) { $env:CANETA_HOME } else { Join-Path $HOME ".caneta" }) "config.json"
$semantic = ($env:CANETA_SEMANTIC -eq "1") -or ((Test-Path $cfg) -and ((Get-Content $cfg -Raw) -match '"semantic":\s*true'))
if ($semantic) { $extra += @("--with", "fastembed>=0.9") }
if ($env:CANETA_SOURCE) {
    uv tool install --python 3.12 --force @extra $env:CANETA_SOURCE
} else {
    uv tool install --python 3.12 --force @extra "caneta @ git+$repo@$ref"
}
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar a CANETA." }
uv tool update-shell | Out-Null
$exe = Join-Path (uv tool dir --bin) "caneta.exe"
if ($semantic) {
    Write-Host "      Baixando o modelo da busca por significado (~0,5 GB, so na primeira vez)..."
    & $exe --enable-semantic
    if ($LASTEXITCODE -ne 0) { Write-Host "      (nao foi ativada; tente depois em Configuracoes)" }
}

Write-Host "[3/3] Criando atalho no Menu Iniciar..."
try {
    $shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "CANETA.lnk"
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    $link.TargetPath = $exe
    $link.Description = "CANETA - escrita cientifica aberta"
    $link.Save()
} catch {
    Write-Host "  (nao foi possivel criar o atalho; use o comando 'caneta')"
}

Write-Host ""
Write-Host "  Pronto! Para abrir depois: menu Iniciar > CANETA, ou o comando 'caneta'." -ForegroundColor Green
Write-Host "  A janela do servidor precisa ficar aberta enquanto voce usa a CANETA."
Write-Host ""
& $exe
