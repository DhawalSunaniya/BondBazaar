"""
BondBazaar — application configuration.

Priority (highest → lowest):
  1. Real environment variables (Render / Docker / shell)
  2. .env file (local dev only; never deployed)
  3. Defaults defined here

In ENVIRONMENT=production the app will refuse to start if any secret is
missing or still set to a known-insecure placeholder.

In ENVIRONMENT=development (default) missing per-site secrets are auto-
generated with secrets.token_urlsafe(32) and a WARNING is logged.
INTERNAL_API_KEY and SHARED_IDENTITY_SALT are special: they must be
identical across all four sibling sites, so they are never auto-generated.
If they are missing, INTERNAL_API_ENABLED is silently treated as False.
"""

import logging
import secrets
import sys
from typing import List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("bondbazaar.config")

# ── Known insecure placeholder values that must not reach production ──────────
_INSECURE_VALUES = {
    "admin123",
    "bb_super_secret_session_key_2026_bond_bazaar",
    "bb_whsec_secret_key_demo_8839",
    "changeme",
    "secret",
    "password",
    "",
}

# ── Secrets that are auto-generated in dev when absent ────────────────────────
_AUTO_GEN_SECRETS = ("SECRET_KEY", "SESSION_SECRET", "WEBHOOK_SIGNING_SECRET", "ADMIN_PASSWORD")

# ── Shared secrets — must match all four sibling sites; never auto-generated ──
_SHARED_SECRETS = ("INTERNAL_API_KEY", "SHARED_IDENTITY_SALT")

# ── All production-required secrets ───────────────────────────────────────────
_PRODUCTION_REQUIRED = set(_AUTO_GEN_SECRETS) | set(_SHARED_SECRETS)


class Settings(BaseSettings):
    # ── Platform ──────────────────────────────────────────────────────────────
    PLATFORM_NAME: str = "BondBazaar"
    ENVIRONMENT: str = "development"          # development | production

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./bondbazaar.db"

    # ── Session / Auth secrets ────────────────────────────────────────────────
    SECRET_KEY: str = ""                      # No real default — generated in dev
    SESSION_SECRET: str = ""
    ACCESS_TOKEN_EXPIRY_DAYS: int = 90
    ADMIN_PASSWORD: str = ""

    # ── Webhook ───────────────────────────────────────────────────────────────
    WEBHOOK_SIGNING_SECRET: str = ""

    # ── Demo / Sandbox ────────────────────────────────────────────────────────
    DEMO_OTP: str = "123456"
    BASE_URL: str = "http://localhost:8000"
    CORS_ORIGINS: str = "*"

    # ── Google OAuth ──────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""

    # ── Comma-separated allow-lists (parsed into lists by properties) ─────────
    ALLOWED_REDIRECT_URIS: str = "http://localhost:3000/link-complete,https://oauth.pstmn.io/v1/callback,http://127.0.0.1:3000/link-complete"
    ALLOWED_WEBHOOK_URLS: str = "http://localhost:8000/webhooks/bondbazaar,http://127.0.0.1:8000/webhooks/bondbazaar,https://webhook.site/demo"
    ALLOWED_EMAILS: str = ""                  # Optional allowlist; empty = all emails allowed

    # ── TradeOne / Broker identity ────────────────────────────────────────────
    BROKER_NAME: str = "BondBazaar"
    PROVIDER_CODE: str = "c"
    DP_NAME: str = "BondBazaar Depository Services"
    DP_ID: str = "IN300003"
    DEPOSITORY_URL: str = ""                  # Optional external depository URL

    # ── TradeOne Internal API — shared secrets ────────────────────────────────
    INTERNAL_API_ENABLED: bool = True
    INTERNAL_API_KEY: str = "tradeone-internal-secret-key"
    SHARED_IDENTITY_SALT: str = "tradeone-shared-identity-salt"
    TRADEONE_URL: str = ""

    # ── Portfolio seeding ─────────────────────────────────────────────────────
    STARTING_FUNDS: float = 1_000_000.0      # ₹10,00,000 default wallet
    SEED_STARTER_PORTFOLIO: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="allow")

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_postgres_url(cls, v: str) -> str:
        """Render / Heroku export 'postgres://' but SQLAlchemy needs 'postgresql://'."""
        if isinstance(v, str) and v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        return v

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def normalise_env(cls, v: str) -> str:
        v = str(v).strip().lower()
        if v not in ("development", "production"):
            raise ValueError(f"ENVIRONMENT must be 'development' or 'production', got: {v!r}")
        return v

    @model_validator(mode="after")
    def harden_secrets(self) -> "Settings":
        env = self.ENVIRONMENT
        is_prod = env == "production"

        missing_in_prod: list = []
        insecure_in_prod: list = []

        # ── Auto-generate per-site secrets in dev ─────────────────────────────
        for name in _AUTO_GEN_SECRETS:
            current = getattr(self, name, "")
            if not current or current in _INSECURE_VALUES:
                if is_prod:
                    if not current:
                        missing_in_prod.append(name)
                    else:
                        insecure_in_prod.append(name)
                else:
                    generated = secrets.token_urlsafe(32)
                    object.__setattr__(self, name, generated)
                    logger.warning(
                        "CONFIG [%s] is not set — auto-generated for this session. "
                        "Set it permanently in your .env file or Render environment.",
                        name,
                    )

        # ── Shared secrets — never auto-generated ─────────────────────────────
        for name in _SHARED_SECRETS:
            current = getattr(self, name, "")
            if not current or current in _INSECURE_VALUES:
                if is_prod:
                    missing_in_prod.append(name)
                else:
                    logger.warning(
                        "CONFIG [%s] is not set. "
                        "INTERNAL_API_ENABLED will be forced to False until this is provided. "
                        "It must be identical on all four sibling sites — run scripts/setup_env.py.",
                        name,
                    )

        # ── Disable internal API when shared secrets are absent ───────────────
        key_ok = bool(self.INTERNAL_API_KEY and self.INTERNAL_API_KEY not in _INSECURE_VALUES)
        salt_ok = bool(self.SHARED_IDENTITY_SALT and self.SHARED_IDENTITY_SALT not in _INSECURE_VALUES)
        if self.INTERNAL_API_ENABLED and not (key_ok and salt_ok):
            object.__setattr__(self, "INTERNAL_API_ENABLED", False)
            logger.warning(
                "CONFIG INTERNAL_API_ENABLED forced to False because "
                "INTERNAL_API_KEY or SHARED_IDENTITY_SALT is missing or insecure."
            )

        # ── Production startup guard ───────────────────────────────────────────
        if is_prod and (missing_in_prod or insecure_in_prod):
            lines = []
            if missing_in_prod:
                lines.append("  Missing : " + ", ".join(missing_in_prod))
            if insecure_in_prod:
                lines.append("  Insecure: " + ", ".join(insecure_in_prod))
            msg = (
                "\n\n🚨  BondBazaar refuses to start in ENVIRONMENT=production "
                "with insecure or missing secrets:\n" + "\n".join(lines) +
                "\n\nSet these variables in your Render environment or .env file.\n"
            )
            logger.critical(msg)
            sys.exit(1)

        return self

    # ── Parsed list helpers ───────────────────────────────────────────────────

    @property
    def allowed_redirect_uris_list(self) -> List[str]:
        return [u.strip() for u in self.ALLOWED_REDIRECT_URIS.split(",") if u.strip()]

    @property
    def allowed_webhook_urls_list(self) -> List[str]:
        return [u.strip() for u in self.ALLOWED_WEBHOOK_URLS.split(",") if u.strip()]

    @property
    def allowed_emails_list(self) -> Optional[List[str]]:
        if not self.ALLOWED_EMAILS.strip():
            return None
        return [e.strip().lower() for e in self.ALLOWED_EMAILS.split(",") if e.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def log_startup_audit(self) -> None:
        """Log each setting NAME and whether it is set. Never logs values."""
        lines = ["", "── BondBazaar Configuration Audit ──────────────────────────"]
        secret_names = set(_AUTO_GEN_SECRETS) | set(_SHARED_SECRETS)
        for field_name in self.model_fields:
            val = getattr(self, field_name, None)
            if field_name in secret_names:
                status = "SET ✓" if (val and val not in _INSECURE_VALUES) else "MISSING ✗"
            else:
                status = "SET ✓" if val not in (None, "", False, 0) else "default/empty"
            lines.append(f"  {field_name:<35} {status}")
        lines.append("────────────────────────────────────────────────────────────")
        logger.info("\n".join(lines))


settings = Settings()

