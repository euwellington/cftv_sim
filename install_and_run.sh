#!/usr/bin/env bash
# cftv_sim - instala (venv + dependencias + binarios) e executa. Linux/macOS.
# Uso: ./install_and_run.sh [--no-run] [--skip-bin]
set -euo pipefail
cd "$(dirname "$0")"

NO_RUN=0
SKIP_BIN=0
for a in "$@"; do
  case "$a" in
    --no-run) NO_RUN=1 ;;
    --skip-bin) SKIP_BIN=1 ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "opcao desconhecida: $a" >&2; exit 2 ;;
  esac
done

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.11+ nao encontrado. Instale (ex.: sudo apt install python3 python3-venv)." >&2
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11+ e necessario (encontrado: $("$PY" -V))." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "==> Criando ambiente virtual (.venv)"
  "$PY" -m venv .venv
fi
VPY=".venv/bin/python"

echo "==> Instalando dependencias"
"$VPY" -m pip install --upgrade pip >/dev/null
"$VPY" -m pip install -r requirements.txt

if [ "$SKIP_BIN" -eq 0 ]; then
  echo "==> Baixando binarios (MediaMTX, FFmpeg, cloudflared)"
  "$VPY" tools/fetch_binaries.py || echo "AVISO: alguns binarios nao foram baixados (veja acima)."
fi

# No Linux, o Qt precisa de libs do sistema (xcb). Dica se faltar:
#   sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1

if [ "$NO_RUN" -eq 1 ]; then
  echo "==> Instalacao concluida. Execute: .venv/bin/python app.py"
  exit 0
fi

echo "==> Iniciando o CFTV Simulator"
exec "$VPY" app.py
