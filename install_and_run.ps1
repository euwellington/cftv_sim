<#
 cftv_sim - instala (venv + dependencias + binarios) e executa. Windows (PowerShell).
 Uso:  powershell -ExecutionPolicy Bypass -File .\install_and_run.ps1 [-NoRun] [-SkipBin]
#>
param(
  [switch]$NoRun,
  [switch]$SkipBin
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Python {
  $cands = @(@("py", "-3"), @("python"))
  foreach ($c in $cands) {
    if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
      $args2 = @()
      if ($c.Length -gt 1) { $args2 = $c[1..($c.Length - 1)] }
      & $c[0] @args2 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) { return ,$c }
    }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Host "Python 3.11+ nao encontrado. Instale em https://www.python.org/downloads/ (marque 'Add to PATH')." -ForegroundColor Red
  exit 1
}
$pyExe = $py[0]
$pyArgs = @()
if ($py.Length -gt 1) { $pyArgs = $py[1..($py.Length - 1)] }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "==> Criando ambiente virtual (.venv)"
  & $pyExe @pyArgs -m venv .venv
}
$vpy = ".venv\Scripts\python.exe"

Write-Host "==> Instalando dependencias"
& $vpy -m pip install --upgrade pip | Out-Null
& $vpy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Falha ao instalar dependencias." -ForegroundColor Red; exit 1 }

if (-not $SkipBin) {
  Write-Host "==> Baixando binarios (mediamtx.exe, ffmpeg.exe, cloudflared.exe) em bin\"
  & $vpy tools\fetch_binaries.py
  if ($LASTEXITCODE -ne 0) { Write-Host "AVISO: alguns binarios nao foram baixados." -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "Lembretes do Windows:" -ForegroundColor Cyan
Write-Host " - Libere o Firewall do Windows para as portas usadas."
Write-Host " - Portas abaixo de 1024 podem exigir Administrador; o padrao e 8080/8554."
Write-Host " - A webcam e aberta com CAP_DSHOW."

if ($NoRun) {
  Write-Host "Instalacao concluida. Execute: .venv\Scripts\pythonw.exe app.py"
  exit 0
}
# pythonw = sem janela de terminal
Start-Process -FilePath ".venv\Scripts\pythonw.exe" -ArgumentList "app.py" -WorkingDirectory $PSScriptRoot
