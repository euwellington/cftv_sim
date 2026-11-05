@echo off
rem cftv_sim - instala (venv + dependencias + binarios) e executa. Windows.
rem Uso: install_and_run.bat [--no-run] [--skip-bin]
setlocal EnableExtensions
cd /d "%~dp0"

set NO_RUN=0
set SKIP_BIN=0
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--no-run" set NO_RUN=1
if /i "%~1"=="--skip-bin" set SKIP_BIN=1
shift
goto parse
:parsed

rem --- localiza o Python 3.11+ (launcher "py" ou "python") ---
set "PY="
where py >nul 2>nul && (py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul && set "PY=py -3")
if not defined PY (
  where python >nul 2>nul && (python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul && set "PY=python")
)
if not defined PY (
  echo Python 3.11+ nao encontrado. Instale em https://www.python.org/downloads/ ^(marque "Add to PATH"^).
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo ==^> Criando ambiente virtual .venv
  %PY% -m venv .venv || goto :erro
)
set "VPY=.venv\Scripts\python.exe"

echo ==^> Instalando dependencias
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt || goto :erro

if "%SKIP_BIN%"=="0" (
  echo ==^> Baixando binarios ^(mediamtx.exe, ffmpeg.exe, cloudflared.exe^) em bin\
  "%VPY%" tools\fetch_binaries.py
  if errorlevel 1 echo AVISO: alguns binarios nao foram baixados ^(veja acima^).
)

echo.
echo Lembretes do Windows:
echo  - Libere o Firewall do Windows para as portas usadas ^(o app mostra a lista na tela Resumo^).
echo  - Portas abaixo de 1024 podem exigir executar como Administrador; o padrao e 8080/8554.
echo  - A webcam e aberta com CAP_DSHOW.
echo.

if "%NO_RUN%"=="1" (
  echo Instalacao concluida. Execute: .venv\Scripts\pythonw.exe app.py
  exit /b 0
)

rem pythonw = sem janela de terminal: toda a interacao e pela GUI
start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:erro
echo.
echo ERRO na instalacao. Veja as mensagens acima.
pause
exit /b 1
