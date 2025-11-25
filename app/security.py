import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_settings

_BCRYPT_MAX_BYTES = 72
_MAX_PASSWORD_CHARS = 64
settings = get_settings()
SESSION_COOKIE_NAME = settings.session_cookie_name
serializer = URLSafeTimedSerializer(settings.secret_key.get_secret_value())


def _prepare_password_bytes(password: str) -> bytes:
    if len(password) > _MAX_PASSWORD_CHARS:
        raise ValueError(f"Password must be at most {_MAX_PASSWORD_CHARS} characters")
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > _BCRYPT_MAX_BYTES:
        raise ValueError("Password exceeds bcrypt 72-byte limit")
    return password_bytes


def hash_password(password: str) -> str:
    password_bytes = _prepare_password_bytes(password)
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    password_bytes = _prepare_password_bytes(password)
    try:
        return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))
    except ValueError as exc:
        raise ValueError("Invalid stored password hash") from exc


def create_session_token(user_id: int) -> str:
    payload = {"uid": user_id, "nonce": secrets.token_hex(8)}
    return serializer.dumps(payload)


def decode_session_token(token: str) -> Optional[int]:
    try:
        max_age = settings.session_expire_minutes * 60
        data = serializer.loads(token, max_age=max_age)
        return data.get("uid")
    except (SignatureExpired, BadSignature):
        return None
