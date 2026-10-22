<#
  CineStream - sobe (ou derruba) o ambiente local.

  Uso:
    .\dev.ps1          # sobe tudo
    .\dev.ps1 down     # desliga o SQL Server (os dados ficam salvos no volume)
#>
param(
    [ValidateSet("up", "down")]
    [string]$Acao = "up"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot
$relogio = [System.Diagnostics.Stopwatch]::StartNew()

# ---------------------------------------------------------------------
# Visual
# ---------------------------------------------------------------------
$linha = "─" * 48

function Show-Header([string]$Titulo) {
    Write-Host ""
    Write-Host "  🎬 CineStream · $Titulo" -ForegroundColor White
    Write-Host "  $linha" -ForegroundColor DarkGray
}

# Linha de status. Com -Pendente, a próxima chamada sobrescreve a mesma linha.
function Show-Step {
    param([string]$Icone, [string]$Nome, [string]$Status, [string]$Cor = "Gray", [switch]$Pendente)
    Write-Host ("`r  {0}  {1,-13}" -f $Icone, $Nome) -NoNewline
    Write-Host $Status.PadRight(45) -ForegroundColor $Cor -NoNewline:$Pendente
}

function Stop-Falha([string]$Icone, [string]$Nome, [string]$Mensagem) {
    Show-Step $Icone $Nome "🔴 $Mensagem" Red
    Write-Host ""
    exit 1
}

function Test-Docker {
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------
# down
# ---------------------------------------------------------------------
if ($Acao -eq "down") {
    Show-Header "desligando o ambiente"
    Show-Step "💾" "SQL Server" "🟡 desligando..." Yellow -Pendente
    docker compose down *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Falha "💾" "SQL Server" "não consegui desligar (o Docker está aberto?)" }
    Show-Step "💾" "SQL Server" "🔴 desligado (dados salvos no volume)" Red
    Write-Host "  $linha" -ForegroundColor DarkGray
    Write-Host "  👋 Até a próxima!" -ForegroundColor White
    Write-Host ""
    exit 0
}

# ---------------------------------------------------------------------
# up
# ---------------------------------------------------------------------
Show-Header "subindo o ambiente"

# 1. Docker Desktop
if (Test-Docker) {
    Show-Step "🐳" "Docker" "🟢 rodando" Green
} else {
    Show-Step "🐳" "Docker" "🟡 iniciando o Docker Desktop..." Yellow -Pendente
    # Docker Desktop 4.37+ tem comando próprio para iniciar, independente de onde foi instalado
    docker desktop start --detach *> $null
    if ($LASTEXITCODE -ne 0) {
        # Plano B: procura o executável a partir do docker.exe que está no PATH
        $cli = (Get-Command docker -ErrorAction SilentlyContinue).Source
        $exe = if ($cli) { Join-Path (Split-Path $cli) "..\..\Docker Desktop.exe" }
        if (-not $exe -or -not (Test-Path $exe)) { Stop-Falha "🐳" "Docker" "não encontrei o Docker Desktop, abra manualmente" }
        Start-Process (Resolve-Path $exe)
    }
    $espera = 0
    while (-not (Test-Docker)) {
        if ($espera -ge 120) { Stop-Falha "🐳" "Docker" "não respondeu em 2 minutos" }
        Start-Sleep -Seconds 3
        $espera += 3
        Show-Step "🐳" "Docker" "🟡 iniciando o Docker Desktop... ${espera}s" Yellow -Pendente
    }
    Show-Step "🐳" "Docker" "🟢 rodando" Green
}

# 2. Ambiente Python
if (Test-Path ".venv") {
    Show-Step "🐍" "Python" "🟢 .venv pronto" Green
} else {
    Show-Step "🐍" "Python" "🟡 criando .venv e instalando dependências..." Yellow -Pendente
    python -m venv .venv *> $null
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip *> $null
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Falha "🐍" "Python" "erro no pip install (rode manualmente para ver)" }
    Show-Step "🐍" "Python" "🟢 .venv criado" Green
}

# 3. SQL Server
Show-Step "💾" "SQL Server" "🟡 subindo e aguardando ficar saudável..." Yellow -Pendente
$saida = docker compose up -d --wait 2>&1
if ($LASTEXITCODE -ne 0) {
    Show-Step "💾" "SQL Server" "🔴 não subiu" Red
    $saida | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
    Write-Host "       Veja os logs: docker compose logs sqlserver" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}
Show-Step "💾" "SQL Server" "🟢 pronto em localhost:1433" Green

# Resumo
Write-Host "  $linha" -ForegroundColor DarkGray
$segundos = [math]::Round($relogio.Elapsed.TotalSeconds)
Write-Host "  ✅ Ambiente no ar em ${segundos}s" -ForegroundColor Green
Write-Host "  💡 Para desligar: .\dev.ps1 down" -ForegroundColor DarkGray
Write-Host ""