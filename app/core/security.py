import logging
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()
log = logging.getLogger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────────


class JWTPayload(TypedDict):
    """
    The exact shape of our JWT payload.
    Using TypedDict instead of dict[str, Any] means callers get
    type-checked field access instead of unchecked .get() calls.
    """

    sub: str  # username
    role: str  # PossibleRoles value
    exp: datetime  # set automatically by create_access_token


# ── Password hashing ──────────────────────────────────────────────────────────
# Argon2 is the winner of the Password Hashing Competition and is strongly
# recommended over bcrypt for new projects. passlib abstracts the parameters.
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ── JWT ───────────────────────────────────────────────────────────────────────


def create_access_token(
    data: JWTPayload,
    expires_delta: timedelta | None = None,
) -> str:
    to_encode: dict = dict(data)
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> JWTPayload | None:
    """Returns the decoded payload or None if the token is invalid/expired."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return JWTPayload(
            sub=payload["sub"],
            role=payload["role"],
            exp=payload["exp"],
        )
    except ExpiredSignatureError:
        log.debug("JWT rejected: token has expired")
        return None
    except (JWTError, KeyError) as exc:
        log.debug("JWT rejected: %s", exc)
        return None
