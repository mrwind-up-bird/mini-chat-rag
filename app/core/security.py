"""Security utilities: encryption, hashing, and token helpers."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from jose import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# ── Password / token hashing (Argon2) ────────────────────────

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── API token hashing (SHA-256, deterministic for lookups) ────

def hash_api_token(raw_token: str) -> str:
    """One-way SHA-256 hash for API token storage.

    We use SHA-256 (not Argon2) because we need to look up tokens
    by their hash on every request — it must be deterministic and fast.
    The raw token has 256 bits of entropy, so brute-force is infeasible.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_api_token() -> str:
    """Generate a cryptographically secure 256-bit API token."""
    return secrets.token_urlsafe(32)


# ── Field-level encryption (Fernet) ──────────────────────────

def _get_fernet() -> Fernet:
    if not settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    return Fernet(settings.encryption_key.encode())


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns base64 ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted value."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ── JWT ───────────────────────────────────────────────────────

def create_jwt(subject: str, tenant_id: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    payload = {
        "sub": subject,
        "tid": tenant_id,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> dict:
    """Decode and verify a JWT. Raises jose.JWTError on failure."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
