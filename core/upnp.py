"""UPnP IGD (stdlib): abre portas no roteador automaticamente e detecta CGNAT.

``open_ports`` descobre o roteador (SSDP), cria os mapeamentos TCP e informa o IP externo que o
roteador enxerga. Se esse IP for privado/CGNAT (10.x, 100.64/10, 192.168.x), a porta NAO sera
alcancavel pela internet, mesmo mapeada.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

SSDP_ADDR = ("239.255.255.250", 1900)
ST_LIST = ("urn:schemas-upnp-org:device:InternetGatewayDevice:1", "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
           "upnp:rootdevice")
SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
            "urn:schemas-upnp-org:service:WANPPPConnection:1")


class UpnpError(RuntimeError):
    pass


@dataclass
class Igd:
    control_url: str
    service: str
    location: str


def discover(timeout: float = 3.0, location: str = "") -> Igd:
    """Acha o roteador via SSDP (ou usa ``location`` direto, util em testes)."""
    locations: list[str] = [location] if location else []
    if not locations:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(timeout)
        try:
            for st in ST_LIST:
                msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                       f"MX: 2\r\nST: {st}\r\n\r\n").encode()
                sock.sendto(msg, SSDP_ADDR)
            while True:
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    break
                m = re.search(rb"(?im)^location:\s*(\S+)", data)
                if m and m.group(1).decode() not in locations:
                    locations.append(m.group(1).decode())
        finally:
            sock.close()
    if not locations:
        raise UpnpError("nenhum roteador respondeu ao UPnP (UPnP desligado no roteador?)")
    for loc in locations:
        try:
            with urllib.request.urlopen(loc, timeout=4) as r:
                root = ET.fromstring(r.read())
        except Exception:  # noqa: BLE001
            continue
        for svc in root.iter():
            if not svc.tag.endswith("service"):
                continue
            st = next((c.text for c in svc if c.tag.endswith("serviceType")), "") or ""
            ctl = next((c.text for c in svc if c.tag.endswith("controlURL")), "") or ""
            if st in SERVICES and ctl:
                return Igd(urljoin(loc, ctl), st, loc)
    raise UpnpError("roteador achado, mas sem servico WANIPConnection/WANPPPConnection")


def _soap(igd: Igd, action: str, args: dict[str, str]) -> str:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    env = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
           f'<u:{action} xmlns:u="{igd.service}">{body}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(igd.control_url, data=env, headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{igd.service}#{action}"'})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        txt = exc.read().decode("utf-8", "replace")
        m = re.search(r"<errorDescription>(.*?)</errorDescription>", txt)
        raise UpnpError(f"{action}: {m.group(1) if m else exc.code}") from exc


def external_ip(igd: Igd) -> str:
    out = _soap(igd, "GetExternalIPAddress", {})
    m = re.search(r"<NewExternalIPAddress>(.*?)</NewExternalIPAddress>", out)
    return m.group(1) if m else ""


def add_mapping(igd: Igd, port: int, internal_ip: str, desc: str = "cftv_sim", proto: str = "TCP") -> None:
    _soap(igd, "AddPortMapping", {
        "NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": proto, "NewInternalPort": str(port),
        "NewInternalClient": internal_ip, "NewEnabled": "1", "NewPortMappingDescription": desc,
        "NewLeaseDuration": "0"})


def delete_mapping(igd: Igd, port: int, proto: str = "TCP") -> None:
    _soap(igd, "DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": proto})


def is_cgnat_or_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a in ipaddress.ip_network("100.64.0.0/10")


def open_ports(ports: list[int], internal_ip: str, location: str = "") -> dict:
    """Mapeia as portas. Retorna ``{"external_ip", "cgnat", "opened", "errors"}``."""
    igd = discover(location=location)
    ext = ""
    try:
        ext = external_ip(igd)
    except UpnpError:
        pass
    opened, errors = [], {}
    for p in ports:
        try:
            add_mapping(igd, p, internal_ip)
            opened.append(p)
        except UpnpError as exc:
            errors[p] = str(exc)
    return {"external_ip": ext, "cgnat": bool(ext) and is_cgnat_or_private(ext), "opened": opened, "errors": errors}
