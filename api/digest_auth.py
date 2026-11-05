"""HTTP/RTSP Digest (RFC 7616 / RFC 2617, qop="auth", MD5) e Basic opcional.

* Nonce autoassinado (HMAC) com validade: nao precisa de estado do servidor.
* ``nc`` e verificado por nonce (rejeita repeticao) em tabela pequena com expiracao.
* O HA1 vem do ``ha1_lookup(usuario, realm)`` (nunca ha senha em claro).
* Basic e opcional (``allow_basic``) e usa ``basic_verify(usuario, senha)``.
* MD5 e imposto pelo protocolo Digest; nao e usado para guardar senhas.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

_PARAM_RE = re.compile(r'(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]*))')


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()


def parse_auth_params(header: str) -> dict[str, str]:
    """``Digest a="b", c=d`` -> {"a": "b", "c": "d"} (sem o esquema)."""
    body = header.split(None, 1)[1] if " " in header.strip() else ""
    return {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3)) for m in _PARAM_RE.finditer(body)}


@dataclass
class AuthResult:
    ok: bool
    username: str = ""
    stale: bool = False  # nonce expirado: cliente deve repetir sem contar falha
    credentials_sent: bool = False  # havia credenciais (para contar tentativas erradas)


class DigestAuthenticator:
    """Autenticador por equipamento (realm proprio)."""

    def __init__(self, realm: str, ha1_lookup: Callable[[str, str], Optional[str]], *, opaque: str | None = None,
                 allow_basic: bool = False, basic_verify: Callable[[str, str], bool] | None = None,
                 nonce_ttl: float = 300.0, secret: bytes | None = None, nonce_style: str = "hex16") -> None:
        self.realm = realm
        self.ha1_lookup = ha1_lookup
        self.opaque = opaque
        self.allow_basic = allow_basic
        self.basic_verify = basic_verify
        self.nonce_ttl = nonce_ttl
        self._secret = secret or secrets.token_bytes(32)
        self.nonce_style = nonce_style
        self._nc: dict[str, int] = {}
        self._lock = threading.Lock()
        self._basic_cache: dict[str, float] = {}

    # ------------------------------------------------------------------ nonce
    def make_nonce(self) -> str:
        """``hex(timestamp)`` + assinatura. Formato curto (16 hex) imita o equipamento real."""
        ts = format(int(time.time()), "08x")
        sig = hmac.new(self._secret, ts.encode(), hashlib.sha256).hexdigest()[:8]
        return ts + sig if self.nonce_style == "hex16" else ts + sig + secrets.token_hex(8)

    def _nonce_valid(self, nonce: str) -> tuple[bool, bool]:
        """(assinatura valida, expirado)."""
        if len(nonce) < 16:
            return False, False
        ts, sig = nonce[:8], nonce[8:16]
        good = hmac.compare_digest(hmac.new(self._secret, ts.encode(), hashlib.sha256).hexdigest()[:8], sig)
        try:
            age = time.time() - int(ts, 16)
        except ValueError:
            return False, False
        return good, age > self.nonce_ttl

    # ------------------------------------------------------------------ desafio
    def challenge(self, stale: bool = False) -> list[str]:
        """Valores de ``WWW-Authenticate`` (Digest e, se habilitado, Basic)."""
        d = f'Digest realm="{self.realm}", nonce="{self.make_nonce()}", qop="auth"'
        if self.opaque:
            d += f', opaque="{self.opaque}"'
        if stale:
            d += ", stale=TRUE"
        out = [d]
        if self.allow_basic:
            out.append(f'Basic realm="{self.realm}"')
        return out

    # ------------------------------------------------------------------ verificacao
    def verify(self, authorization: Optional[str], method: str, request_uri: str | None = None,
               check_uri: bool = True) -> AuthResult:
        """Valida o cabecalho ``Authorization``. ``request_uri`` = alvo da requisicao (path?query)."""
        if not authorization:
            return AuthResult(False)
        scheme = authorization.strip().split(None, 1)[0].lower()
        if scheme == "basic":
            return self._verify_basic(authorization)
        if scheme != "digest":
            return AuthResult(False, credentials_sent=True)
        p = parse_auth_params(authorization)
        user, nonce, uri, resp = p.get("username", ""), p.get("nonce", ""), p.get("uri", ""), p.get("response", "")
        if not (user and nonce and uri and resp):
            return AuthResult(False, credentials_sent=True)
        if p.get("realm") and p["realm"] != self.realm:
            return AuthResult(False, credentials_sent=True)
        if self.opaque and p.get("opaque") != self.opaque:
            return AuthResult(False, credentials_sent=True)
        good, expired = self._nonce_valid(nonce)
        if not good:
            return AuthResult(False, credentials_sent=True)
        if expired:
            return AuthResult(False, stale=True, credentials_sent=False)
        if check_uri and request_uri is not None and not _same_target(uri, request_uri):
            return AuthResult(False, credentials_sent=True)
        ha1 = self.ha1_lookup(user, self.realm)
        if not ha1:
            return AuthResult(False, user, credentials_sent=True)
        ha2 = md5_hex(f"{method}:{uri}")
        qop = p.get("qop", "")
        if qop:
            if qop != "auth":
                return AuthResult(False, user, credentials_sent=True)
            nc, cnonce = p.get("nc", ""), p.get("cnonce", "")
            if not nc or not cnonce:
                return AuthResult(False, user, credentials_sent=True)
            expected = md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        else:
            expected = md5_hex(f"{ha1}:{nonce}:{ha2}")
        if not hmac.compare_digest(expected, resp.lower()):
            return AuthResult(False, user, credentials_sent=True)
        if qop and not self._check_nc(nonce, p["nc"]):
            return AuthResult(False, user, credentials_sent=True)
        return AuthResult(True, user, credentials_sent=True)

    def _check_nc(self, nonce: str, nc_hex: str) -> bool:
        """Rejeita repeticao de ``nc`` (anti-replay). Clientes enviam nc crescente."""
        try:
            nc = int(nc_hex, 16)
        except ValueError:
            return False
        with self._lock:
            now = time.monotonic()
            if len(self._nc) > 2000:  # limpeza simples
                self._nc.clear()
            last = self._nc.get(nonce, 0)
            if nc <= last and nc != 1:
                return False
            self._nc[nonce] = nc
        return True

    def _verify_basic(self, authorization: str) -> AuthResult:
        if not (self.allow_basic and self.basic_verify):
            return AuthResult(False, credentials_sent=True)
        try:
            raw = base64.b64decode(authorization.split(None, 1)[1]).decode("utf-8")
            user, _, pw = raw.partition(":")
        except Exception:  # noqa: BLE001
            return AuthResult(False, credentials_sent=True)
        key = hashlib.sha256(f"{user}\0{pw}".encode()).hexdigest()
        now = time.monotonic()
        if self._basic_cache.get(key, 0) > now:  # PBKDF2 e lento: cache curto de sucesso
            return AuthResult(True, user, credentials_sent=True)
        if self.basic_verify(user, pw):
            if len(self._basic_cache) > 500:
                self._basic_cache.clear()
            self._basic_cache[key] = now + 60
            return AuthResult(True, user, credentials_sent=True)
        return AuthResult(False, user, credentials_sent=True)


def _same_target(uri_in_header: str, request_target: str) -> bool:
    """Compara so ``path?query`` (clientes enviam URL absoluta ou relativa)."""
    def norm(u: str) -> str:
        if "://" in u:
            u = "/" + u.split("://", 1)[1].split("/", 1)[1] if "/" in u.split("://", 1)[1] else "/"
        return u
    return norm(uri_in_header) == norm(request_target)


def client_digest_header(username: str, password: str, realm: str, nonce: str, method: str, uri: str,
                         qop: str = "auth", nc: int = 1, cnonce: str | None = None, opaque: str | None = None) -> str:
    """Monta um ``Authorization: Digest`` (usado em testes e no cliente de teste da GUI)."""
    cnonce = cnonce or secrets.token_hex(8)
    ha1 = md5_hex(f"{username}:{realm}:{password}")
    ha2 = md5_hex(f"{method}:{uri}")
    ncs = f"{nc:08x}"
    resp = md5_hex(f"{ha1}:{nonce}:{ncs}:{cnonce}:{qop}:{ha2}") if qop else md5_hex(f"{ha1}:{nonce}:{ha2}")
    h = (f'Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"')
    if qop:
        h += f', qop={qop}, nc={ncs}, cnonce="{cnonce}"'
    if opaque:
        h += f', opaque="{opaque}"'
    return h
