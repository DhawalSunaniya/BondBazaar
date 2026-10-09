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

    # TradeOne / Broker Configuration
    BROKER_NAME: str = "BondBazaar"
    PROVIDER_CODE: str = "c"
    DP_NAME: str = "BondBazaar Depository Services"
    DP_ID: str = "IN300003"

    # Internal API (TradeOne)
    INTERNAL_API_ENABLED: bool = True
    INTERNAL_API_KEY: str = "bb_int_key_4a9b2c8e1f7d5a3b6e8c0d2f4a1e9c7b"
    SHARED_IDENTITY_SALT: str = "tradeone-shared-identity-salt-2026"
    TRADEONE_URL: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="allow")

settings = Settings()

