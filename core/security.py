"""Seguranca: politica de senha, hashes, HA1 do Digest, lockout e sessoes.

Pontos importantes:

* senhas padrao (admin/admin, 12345, 123456...) sao **bloqueadas**;
* o hash do portal e PBKDF2-HMAC-SHA256 (biblioteca padrao);
* o Digest (RFC 7616 / RFC 2617) exige ``HA1 = MD5(user:realm:senha)``.
  MD5 aqui e imposto pelo protocolo, nao e usado para guardar senha;
* ``LoginLimiter`` implementa o "illegal login lock" (5 erros -> bloqueio).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .models import UserRecord

# Senhas proibidas (comparacao em minusculas). Inclui as pedidas no prompt.
FORBIDDEN_PASSWORDS = frozenset({
    "admin", "admin123", "admin1234", "administrator", "root", "12345", "123456", "1234", "1234567",
    "12345678", "123456789", "1234567890", "111111", "000000", "00000000", "888888", "666666",
    "password", "password1", "senha", "senha123", "qwerty", "qwerty123", "abc123", "hikvision",
    "hik12345", "hikvision123", "intelbras", "intelbras123", "dahua", "dahua123", "camera", "camera123",
    "cftv", "cftv123", "guest", "user", "default", "iloveyou", "letmein", "welcome",
})
MIN_LENGTH = 10
MAX_LENGTH = 64


# ----------------------------------------------------------------------------- senha
@dataclass
class PasswordReport:
    score: int  # 0..100
    label: str  # muito fraca, fraca, media, forte, muito forte
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def is_forbidden_password(password: str, username: str = "") -> bool:
    """True para senhas padrao/obvias ou que contem o usuario."""
    p = password.strip().lower()
    if p in FORBIDDEN_PASSWORDS:
        return True
    if username and p == username.strip().lower():
        return True
    return False


def _has_sequence(p: str) -> bool:
    low = p.lower()
    for i in range(len(low) - 3):
        chunk = low[i:i + 4]
        if chunk.isalnum() and all(ord(chunk[j + 1]) - ord(chunk[j]) == 1 for j in range(3)):
            return True
        if chunk.isalnum() and all(ord(chunk[j]) - ord(chunk[j + 1]) == 1 for j in range(3)):
            return True
    return False


def password_strength(password: str, username: str = "", min_length: int = MIN_LENGTH) -> PasswordReport:
    """Avalia a senha. ``issues`` vazio significa que a politica e atendida."""
    issues: list[str] = []
    pw = password or ""
    classes = sum(bool(re.search(rx, pw)) for rx in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))

    if is_forbidden_password(pw, username):
        issues.append("senha padrao/obvia e proibida")
    if len(pw) < min_length:
        issues.append(f"minimo de {min_length} caracteres")
    if len(pw) > MAX_LENGTH:
        issues.append(f"maximo de {MAX_LENGTH} caracteres")
    if classes < 3:
        issues.append("use ao menos 3 tipos: minuscula, MAIUSCULA, numero, simbolo")
    if re.search(r"(.)\1{3,}", pw):
        issues.append("evite 4 ou mais caracteres iguais seguidos")
    if _has_sequence(pw):
        issues.append("evite sequencias (abcd, 1234)")
    if username and len(username) >= 3 and username.lower() in pw.lower():
        issues.append("nao inclua o nome de usuario na senha")

    score = min(len(pw), 20) * 3 + classes * 10
    score += 10 if len(set(pw)) >= 8 else 0
    score = max(0, min(100, score - 12 * len(issues)))
    if is_forbidden_password(pw, username):
        score = min(score, 5)
    label = ("muito fraca", "fraca", "media", "forte", "muito forte")[min(4, score // 21)]
    return PasswordReport(score=score, label=label, issues=issues)


def generate_password(length: int = 14) -> str:
    """Gera uma senha forte aleatoria que atende a politica."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%&*-_"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if password_strength(pw).ok:
            return pw


# ----------------------------------------------------------------------------- hash
_ITER = 240_000


def hash_password(password: str, iterations: int = _ITER) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(it))
        return hmac.compare_digest(got, expected)
    except Exception:  # noqa: BLE001 - hash malformado = invalido
        return False


def make_ha1(username: str, realm: str, password: str) -> str:
    """HA1 do Digest: MD5(user:realm:senha) (exigido pelo protocolo)."""
    return hashlib.md5(f"{username}:{realm}:{password}".encode(), usedforsecurity=False).hexdigest()


# ----------------------------------------------------------------------------- usuarios
class UserStore:
    """Usuarios de um equipamento (opera sobre a lista ``DeviceSpec.users``)."""

    def __init__(self, users: list[UserRecord], realms: list[str] | None = None) -> None:
        self.users = users
        self.realms = list(realms or [])
        self._lock = threading.RLock()

    def get(self, username: str) -> UserRecord | None:
        with self._lock:
            for u in self.users:
                if u.username == username:
                    return u
        return None

    def set_password(self, username: str, password: str, *, role: str | None = None,
                     validate: bool = True, must_change: bool = False) -> UserRecord:
        """Cria/atualiza o usuario, recalculando hash e HA1 de todos os realms."""
        if validate:
            rep = password_strength(password, username)
            if not rep.ok:
                raise ValueError("; ".join(rep.issues))
        with self._lock:
            u = self.get(username)
            if u is None:
                u = UserRecord(username=username, role=role or "viewer")  # type: ignore[arg-type]
                self.users.append(u)
            if role:
                u.role = role  # type: ignore[assignment]
            u.pw_hash = hash_password(password)
            u.ha1 = {r: make_ha1(username, r, password) for r in self.realms}
            u.must_change_password = must_change
            return u

    def add_realm(self, realm: str, username: str, password: str) -> None:
        """Adiciona um realm novo (precisa da senha em claro para calcular o HA1)."""
        with self._lock:
            if realm not in self.realms:
                self.realms.append(realm)
            u = self.get(username)
            if u:
                u.ha1[realm] = make_ha1(username, realm, password)

    def delete(self, username: str) -> bool:
        with self._lock:
            before = len(self.users)
            self.users[:] = [u for u in self.users if u.username != username]
            return len(self.users) != before

    def verify(self, username: str, password: str) -> UserRecord | None:
        u = self.get(username)
        if u and u.enabled and verify_password(password, u.pw_hash):
            return u
        return None

    def digest_ha1(self, username: str, realm: str) -> str | None:
        """HA1 para o servidor Digest; ``None`` se usuario/realm nao existir ou estiver desabilitado."""
        u = self.get(username)
        if u and u.enabled:
            return u.ha1.get(realm)
        return None


# ----------------------------------------------------------------------------- lockout
class LoginLimiter:
    """Bloqueio por falhas (por IP e por usuario), como o "illegal login lock".

    ``fail(key)`` conta uma falha; ao chegar em ``max_attempts`` dentro de
    ``window`` segundos, a chave fica bloqueada por ``lockout`` segundos.
    """

    def __init__(self, max_attempts: int = 5, window: float = 900.0, lockout: float = 1800.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_attempts = max_attempts
        self.window = window
        self.lockout = lockout
        self._clock = clock
        self._fails: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """(permitido, segundos restantes de bloqueio)."""
        with self._lock:
            now = self._clock()
            until = self._locked_until.get(key, 0.0)
            if until > now:
                return False, int(until - now) + 1
            if until:
                self._locked_until.pop(key, None)
            return True, 0

    def fail(self, key: str) -> bool:
        """Registra falha. Retorna True se a chave ficou bloqueada agora."""
        with self._lock:
            now = self._clock()
            hist = [t for t in self._fails.get(key, []) if now - t <= self.window]
            hist.append(now)
            self._fails[key] = hist
            if len(hist) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout
                self._fails.pop(key, None)
                return True
            return False

    def success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)

    def remaining_attempts(self, key: str) -> int:
        with self._lock:
            now = self._clock()
            n = len([t for t in self._fails.get(key, []) if now - t <= self.window])
            return max(0, self.max_attempts - n)


# ----------------------------------------------------------------------------- sessoes (portal)
@dataclass
class Session:
    token: str
    csrf: str
    username: str
    role: str
    ip: str
    expires: float
    must_change_password: bool = False


class SessionManager:
    """Sessoes em memoria (cookie HttpOnly + token CSRF)."""

    def __init__(self, ttl: float = 28800.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str, role: str, ip: str, must_change_password: bool = False) -> Session:
        s = Session(token=secrets.token_urlsafe(32), csrf=secrets.token_urlsafe(24), username=username,
                    role=role, ip=ip, expires=self._clock() + self.ttl,
                    must_change_password=must_change_password)
        with self._lock:
            self._sessions[s.token] = s
        return s

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            s = self._sessions.get(token)
            if s and s.expires <= self._clock():
                self._sessions.pop(token, None)
                return None
            return s

    def destroy(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def destroy_user(self, username: str) -> None:
        with self._lock:
            for t in [t for t, s in self._sessions.items() if s.username == username]:
                self._sessions.pop(t, None)

    def purge(self) -> int:
        now = self._clock()
        with self._lock:
            dead = [t for t, s in self._sessions.items() if s.expires <= now]
            for t in dead:
                self._sessions.pop(t, None)
            return len(dead)


def check_csrf(session: Session | None, token: str | None) -> bool:
    """Comparacao em tempo constante do token CSRF."""
    return bool(session and token and hmac.compare_digest(session.csrf, token))
