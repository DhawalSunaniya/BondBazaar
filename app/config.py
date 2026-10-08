from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PLATFORM_NAME: str = "BondBazaar"
    SECRET_KEY: str = "bb_super_secret_session_key_2026_bond_bazaar"
    ACCESS_TOKEN_EXPIRY_DAYS: int = 90
    ADMIN_PASSWORD: str = "admin123"
    CORS_ORIGINS: str = "*"
    WEBHOOK_SIGNING_SECRET: str = "bb_whsec_secret_key_demo_8839"
    DATABASE_URL: str = "sqlite:///./bondbazaar.db"
    DEMO_OTP: str = "123456"
    BASE_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(env_file=".env", extra="allow")

settings = Settings()
