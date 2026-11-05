#!/usr/bin/env bash
# cftv_sim - instala (venv + dependencias + binarios) e executa. Linux/macOS.
# Uso: ./install_and_run.sh [--no-run] [--skip-bin] [--skip-net]
set -euo pipefail
cd "$(dirname "$0")"

NO_RUN=0
SKIP_BIN=0
SKIP_NET=0
for a in "$@"; do
  case "$a" in
    --no-run) NO_RUN=1 ;;
    --skip-bin) SKIP_BIN=1 ;;
    --skip-net) SKIP_NET=1 ;;
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

# ---- Rede: firewall + UPnP (abre as portas do perfil no roteador) + diagnostico de IP publico
if [ "$SKIP_NET" -eq 0 ]; then
  echo "==> Configurando rede (firewall e roteador)"
  PORTS=$("$VPY" - <<'PYEOF'
import json, glob, os
files = sorted(glob.glob("config/profiles/*.json"), key=os.path.getmtime, reverse=True)
ports = {8800, 8080, 8554}
for f in files[:1]:
    try:
        p = json.load(open(f, encoding="utf-8"))
        ports = {p.get("web_port", 8800)}
        for d in p.get("devices", []):
            ports |= {d.get("http_port"), d.get("rtsp_port")}
    except Exception:
        pass
print(" ".join(str(x) for x in sorted(x for x in ports if x)))
PYEOF
)
  if command -v ufw >/dev/null 2>&1; then
    for p in $PORTS; do sudo ufw allow "$p"/tcp >/dev/null 2>&1 && echo "   ufw: liberada $p/tcp" || echo "   ufw: nao foi possivel liberar $p (rode com sudo)"; done
  elif command -v firewall-cmd >/dev/null 2>&1; then
    for p in $PORTS; do sudo firewall-cmd --permanent --add-port="$p"/tcp >/dev/null 2>&1; done
    sudo firewall-cmd --reload >/dev/null 2>&1 && echo "   firewalld: portas liberadas"
  fi
  "$VPY" tools/net_setup.py || echo "AVISO: acesso externo (IP publico) nao configurado; o acesso na rede local funciona normalmente."
fi

# No Linux, o Qt precisa de libs do sistema (xcb). Dica se faltar:
#   sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1

if [ "$NO_RUN" -eq 1 ]; then
  echo "==> Instalacao concluida. Execute: .venv/bin/python app.py"
  exit 0
fi

echo "==> Iniciando o CFTV Simulator"
exec "$VPY" app.py
