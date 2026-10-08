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
    INTERNAL_API_ENABLED: bool = False
    INTERNAL_API_KEY: str = ""
    SHARED_IDENTITY_SALT: str = "tradeone-shared-identity-salt-2026"
    TRADEONE_URL: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="allow")

settings = Settings()

