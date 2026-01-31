from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/digitaltwin"
    
    # Google OAuth (optional - needed for Google integration)
    google_client_id: str = ""
    google_client_secret: str = ""
    
    # OpenAI
    openai_api_key: str = ""
    
    # Security
    secret_key: str = "dev-secret-key-change-in-production"
    encryption_key: str = ""  # Base64 encoded 32-byte key for Fernet
    
    # URLs
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    
    # Google OAuth URLs
    google_auth_uri: str = "https://accounts.google.com/o/oauth2/auth"
    google_token_uri: str = "https://oauth2.googleapis.com/token"
    
    # Google API Scopes
    google_scopes: list[str] = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/gmail.readonly",
    ]
    
    # Notion OAuth (optional - needed for Notion integration)
    notion_client_id: str = ""
    notion_client_secret: str = ""
    
    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
