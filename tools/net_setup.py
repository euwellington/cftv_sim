#!/usr/bin/env python3
"""Configura a rede para acesso externo: UPnP no roteador + diagnostico (CGNAT/IP publico).

Uso: python tools/net_setup.py [perfil]   (le portas de config/profiles/<perfil>.json; padrao: o mais recente)
Saida: codigo 0 = portas abertas e IP externo alcancavel (em tese); 1 = algo impede (explicado na tela).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import upnp  # noqa: E402
from core.fmt import fetch_public_ip, primary_ip  # noqa: E402


def load_ports(name: str | None) -> tuple[list[int], dict]:
    files = sorted((ROOT / "config" / "profiles").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if name:
        files = [ROOT / "config" / "profiles" / f"{name}.json"]
    for f in files:
        try:
            prof = json.loads(f.read_text(encoding="utf-8"))
            ports = {prof.get("web_port", 8800)}
            for d in prof.get("devices", []):
                ports |= {d.get("http_port"), d.get("rtsp_port")}
            return sorted(p for p in ports if p), prof
        except (OSError, ValueError):
            continue
    return [8800, 8080, 8554], {}


def main() -> int:
    ports, prof = load_ports(sys.argv[1] if len(sys.argv) > 1 else None)
    ip = primary_ip()
    print(f"IP local: {ip}   portas: {', '.join(map(str, ports))}")
    try:
        pub = fetch_public_ip()
    except OSError:
        pub = ""
    print(f"IP publico (internet): {pub or 'nao foi possivel obter'}")
    try:
        r = upnp.open_ports(ports, ip)
    except upnp.UpnpError as exc:
        print(f"UPnP: {exc}")
        print("-> Ligue o UPnP no roteador OU faca Port Forwarding manual (TCP "
              f"{', '.join(map(str, ports))} -> {ip}).")
        return 1
    print(f"UPnP: portas abertas {r['opened'] or 'nenhuma'}  erros {r['errors'] or 'nenhum'}")
    print(f"IP externo visto pelo roteador: {r['external_ip'] or 'desconhecido'}")
    if r["cgnat"]:
        print("!! CGNAT detectado: o roteador NAO tem IP publico proprio. O IP publico NAO vai funcionar.")
        print("-> Use Tailscale (IP 100.x), ngrok TCP, ou peca IP publico ao provedor.")
        return 1
    if pub and r["external_ip"] and pub != r["external_ip"]:
        print(f"!! IP do roteador ({r['external_ip']}) difere do IP publico ({pub}): provavel CGNAT/duplo NAT.")
        return 1
    rtsp = next((d["rtsp_port"] for d in prof.get("devices", [])), 8554)
    print(f"OK. Teste DE FORA da sua rede (4G): vlc \"rtsp://USUARIO:SENHA@{pub or r['external_ip']}:{rtsp}/Streaming/Channels/101\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
