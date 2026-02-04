import base64
import hashlib
import os
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from itsdangerous import URLSafeTimedSerializer

from app.core.config import get_settings

settings = get_settings()

# Cache the AES-GCM key so it's consistent
_aes_key: bytes | None = None

# AES-GCM constants
NONCE_SIZE = 12  # 96 bits - recommended for GCM
KEY_SIZE = 32    # 256 bits


def get_aes_key() -> bytes:
    """Get AES-256 key for token encryption"""
    global _aes_key
    
    if _aes_key is not None:
        return _aes_key
    
    if settings.encryption_key:
        # If provided, hash it to ensure exactly 32 bytes
        _aes_key = hashlib.sha256(settings.encryption_key.encode()).digest()
    else:
        # Derive a stable key from SECRET_KEY for development
        # This ensures the key is consistent across restarts
        _aes_key = hashlib.sha256(settings.secret_key.encode()).digest()
    
    return _aes_key


def encrypt_token(token: str) -> str:
    """
    Encrypt an OAuth token using AES-256-GCM.
    
    Format: base64(nonce || ciphertext || tag)
    - nonce: 12 bytes (unique per encryption)
    - ciphertext: variable length
    - tag: 16 bytes (authentication tag, appended by AESGCM)
    """
    key = get_aes_key()
    aesgcm = AESGCM(key)
    
    # Generate random nonce (MUST be unique per encryption)
    nonce = os.urandom(NONCE_SIZE)
    
    # Encrypt (returns ciphertext + 16-byte auth tag)
    ciphertext = aesgcm.encrypt(nonce, token.encode(), None)
    
    # Combine: nonce + ciphertext (which includes tag)
    encrypted = nonce + ciphertext
    
    return base64.urlsafe_b64encode(encrypted).decode()


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt an OAuth token encrypted with AES-256-GCM.
    
    Raises exception if authentication fails (tampered data).
    """
    key = get_aes_key()
    aesgcm = AESGCM(key)
    
    # Decode from base64
    encrypted = base64.urlsafe_b64decode(encrypted_token.encode())
    
    # Extract nonce and ciphertext
    nonce = encrypted[:NONCE_SIZE]
    ciphertext = encrypted[NONCE_SIZE:]
    
    # Decrypt and verify (raises InvalidTag if tampered)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    
    return plaintext.decode()


def get_serializer() -> URLSafeTimedSerializer:
    """Get serializer for session cookies"""
    return URLSafeTimedSerializer(settings.secret_key)


def create_session_token(user_id: str) -> str:
    """Create a signed session token"""
    serializer = get_serializer()
    return serializer.dumps({"user_id": user_id})


def verify_session_token(token: str, max_age: int = 86400 * 7) -> dict | None:
    """Verify and decode a session token (default 7 days)"""
    serializer = get_serializer()
    try:
        return serializer.loads(token, max_age=max_age)
    except Exception:
        return None


def generate_state_token() -> str:
    """Generate a random state token for OAuth CSRF protection"""
    return secrets.token_urlsafe(32)
