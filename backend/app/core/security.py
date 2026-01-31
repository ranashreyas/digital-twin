import base64
import hashlib
import secrets
from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer

from app.core.config import get_settings

settings = get_settings()

# Cache the fernet instance so it's consistent
_fernet_instance = None


def get_fernet() -> Fernet:
    """Get Fernet instance for token encryption"""
    global _fernet_instance
    
    if _fernet_instance is not None:
        return _fernet_instance
    
    if settings.encryption_key:
        key = settings.encryption_key.encode()
    else:
        # Derive a stable key from SECRET_KEY for development
        # This ensures the key is consistent across restarts
        key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.secret_key.encode()).digest()
        )
    
    _fernet_instance = Fernet(key)
    return _fernet_instance


def encrypt_token(token: str) -> str:
    """Encrypt an OAuth token for storage"""
    fernet = get_fernet()
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt an OAuth token from storage"""
    fernet = get_fernet()
    return fernet.decrypt(encrypted_token.encode()).decode()


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
