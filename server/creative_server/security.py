import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


PASSWORD = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)
DUMMY_PASSWORD_HASH = PASSWORD.hash("not-a-real-account-password-42!")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,63}$")


def validate_username(value: str) -> str:
    normalized = value.strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("账号需为 3–64 位字母、数字、点、横线或下划线")
    return normalized


def validate_password(value: str, username: str = "") -> str:
    if len(value) < 12 or len(value) > 128:
        raise ValueError("密码长度必须为 12–128 位")
    groups = sum(bool(re.search(pattern, value)) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if groups < 3:
        raise ValueError("密码至少包含大小写字母、数字、符号中的三类")
    if username and username.lower() in value.lower():
        raise ValueError("密码不能包含账号名")
    return value


def hash_password(value: str) -> str:
    return PASSWORD.hash(value)


def verify_password(stored: str, candidate: str) -> bool:
    try:
        return PASSWORD.verify(stored, candidate)
    except (VerifyMismatchError, InvalidHashError):
        return False


def opaque_token() -> str:
    return secrets.token_urlsafe(48)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def expiry(days: int) -> datetime:
    return utcnow() + timedelta(days=days)
