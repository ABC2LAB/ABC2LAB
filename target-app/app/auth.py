import hashlib
import hmac
import os
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from .models import User

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), hash_hex)


def get_current_user(request: Request, db: Session) -> Optional[User]:
    """세션 쿠키에서 로그인 사용자를 조회. 비로그인이면 None (=Guest)."""
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()
