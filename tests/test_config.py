"""
tests/test_config.py — Tests for BondBazaar configuration hardening.

Tests:
  1. Config loads from .env via monkeypatching env vars
  2. Env var overrides .env default
  3. Production refuses to start when secrets are missing (sys.exit)
  4. Production refuses to start when secrets equal known insecure values
  5. postgres:// URL is auto-converted to postgresql://
  6. INTERNAL_API_ENABLED is forced False when shared secrets are absent
  7. INTERNAL_API_ENABLED is True when both shared secrets are set
  8. Parsed list properties work correctly (ALLOWED_REDIRECT_URIS, etc.)
"""

import importlib
import os
import sys
import pytest


def _reload_settings(env_overrides: dict) -> "Settings":
    """
    Create an isolated Settings instance built from env_overrides.
    All variables not in env_overrides are removed from os.environ so
    that Settings(_env_file=None) reads exactly what the test provides.
    """
    from app.config import Settings

    old_env = os.environ.copy()
    try:
        # Clear out env vars so Settings only reads what we supply
        for key in list(os.environ.keys()):
            if key not in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOME"):
                del os.environ[key]

        for k, v in env_overrides.items():
            os.environ[k] = v

        result = Settings(_env_file=None)  # type: ignore[call-arg]
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    return result


# ── Helper to build a minimal valid dev environment ──────────────────────────

def _dev_env(**extra) -> dict:
    base = {
        "ENVIRONMENT": "development",
        "DATABASE_URL": "sqlite:///./test.db",
        "INTERNAL_API_KEY": "a-valid-shared-key-for-test",
        "SHARED_IDENTITY_SALT": "a-valid-shared-salt-for-test",
    }
    base.update(extra)
    return base


def _prod_env(**extra) -> dict:
    base = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "sqlite:///./test.db",
        "SECRET_KEY": "prod-secret-key-that-is-long-enough-32x",
        "SESSION_SECRET": "prod-session-secret-32chars-minimum!",
        "ADMIN_PASSWORD": "StrongProdPass!2026",
        "WEBHOOK_SIGNING_SECRET": "prod-webhook-signing-secret-32chars!",
        "INTERNAL_API_KEY": "prod-shared-api-key-identical-all-sites",
        "SHARED_IDENTITY_SALT": "prod-shared-salt-identical-all-sites!!",
    }
    base.update(extra)
    return base


# ── 1. Config loads from environment variables ────────────────────────────────

def test_config_loads_platform_name_from_env():
    s = _reload_settings(_dev_env(PLATFORM_NAME="TestPlatform"))
    assert s.PLATFORM_NAME == "TestPlatform"


def test_config_loads_database_url_from_env():
    s = _reload_settings(_dev_env(DATABASE_URL="sqlite:///./custom.db"))
    assert s.DATABASE_URL == "sqlite:///./custom.db"


# ── 2. Env var overrides default ──────────────────────────────────────────────

def test_env_var_overrides_default_demo_otp():
    s = _reload_settings(_dev_env(DEMO_OTP="999999"))
    assert s.DEMO_OTP == "999999"


def test_env_var_overrides_base_url():
    s = _reload_settings(_dev_env(BASE_URL="https://bondbazaar.onrender.com"))
    assert s.BASE_URL == "https://bondbazaar.onrender.com"


# ── 3. Production refuses to start when secrets are missing ──────────────────

def test_production_exits_on_missing_secret_key(monkeypatch):
    env = _prod_env()
    del env["SECRET_KEY"]  # remove required secret
    with pytest.raises(SystemExit):
        _reload_settings(env)


def test_production_exits_on_missing_internal_api_key(monkeypatch):
    env = _prod_env()
    del env["INTERNAL_API_KEY"]
    with pytest.raises(SystemExit):
        _reload_settings(env)


def test_production_exits_on_missing_shared_identity_salt(monkeypatch):
    env = _prod_env()
    del env["SHARED_IDENTITY_SALT"]
    with pytest.raises(SystemExit):
        _reload_settings(env)


# ── 4. Production refuses insecure placeholder values ────────────────────────

def test_production_exits_on_insecure_admin_password(monkeypatch):
    env = _prod_env(ADMIN_PASSWORD="admin123")
    with pytest.raises(SystemExit):
        _reload_settings(env)


def test_production_exits_on_insecure_secret_key(monkeypatch):
    env = _prod_env(SECRET_KEY="bb_super_secret_session_key_2026_bond_bazaar")
    with pytest.raises(SystemExit):
        _reload_settings(env)


# ── 5. postgres:// → postgresql:// URL fix ────────────────────────────────────

def test_postgres_url_converted_to_postgresql():
    s = _reload_settings(_dev_env(DATABASE_URL="postgres://user:pass@host:5432/db"))
    assert s.DATABASE_URL.startswith("postgresql://")
    assert "user:pass@host:5432/db" in s.DATABASE_URL


def test_postgresql_url_unchanged():
    s = _reload_settings(_dev_env(DATABASE_URL="postgresql://user:pass@host:5432/db"))
    assert s.DATABASE_URL.startswith("postgresql://")


def test_sqlite_url_unchanged():
    s = _reload_settings(_dev_env(DATABASE_URL="sqlite:///./bondbazaar.db"))
    assert s.DATABASE_URL == "sqlite:///./bondbazaar.db"


# ── 6. INTERNAL_API_ENABLED forced False when shared secrets absent ───────────

def test_internal_api_disabled_when_key_missing():
    env = _dev_env()
    del env["INTERNAL_API_KEY"]
    s = _reload_settings(env)
    assert s.INTERNAL_API_ENABLED is False


def test_internal_api_disabled_when_salt_missing():
    env = _dev_env()
    del env["SHARED_IDENTITY_SALT"]
    s = _reload_settings(env)
    assert s.INTERNAL_API_ENABLED is False


def test_internal_api_disabled_when_both_missing():
    env = _dev_env()
    del env["INTERNAL_API_KEY"]
    del env["SHARED_IDENTITY_SALT"]
    s = _reload_settings(env)
    assert s.INTERNAL_API_ENABLED is False


# ── 7. INTERNAL_API_ENABLED True when both shared secrets are present ─────────

def test_internal_api_enabled_when_secrets_set():
    s = _reload_settings(_dev_env(
        INTERNAL_API_ENABLED="true",
        INTERNAL_API_KEY="valid-shared-key",
        SHARED_IDENTITY_SALT="valid-shared-salt",
    ))
    assert s.INTERNAL_API_ENABLED is True


# ── 8. Parsed list properties ─────────────────────────────────────────────────

def test_allowed_redirect_uris_list_parsed():
    s = _reload_settings(_dev_env(
        ALLOWED_REDIRECT_URIS="http://a.com/cb,http://b.com/cb, http://c.com/cb "
    ))
    uris = s.allowed_redirect_uris_list
    assert len(uris) == 3
    assert "http://a.com/cb" in uris
    assert "http://c.com/cb" in uris  # stripped


def test_allowed_webhook_urls_list_parsed():
    s = _reload_settings(_dev_env(
        ALLOWED_WEBHOOK_URLS="https://hook1.site/x,https://hook2.site/y"
    ))
    urls = s.allowed_webhook_urls_list
    assert len(urls) == 2


def test_allowed_emails_list_none_when_empty():
    s = _reload_settings(_dev_env(ALLOWED_EMAILS=""))
    assert s.allowed_emails_list is None


def test_allowed_emails_list_parsed_and_lowercased():
    s = _reload_settings(_dev_env(ALLOWED_EMAILS="Alice@example.com, BOB@test.org"))
    lst = s.allowed_emails_list
    assert lst is not None
    assert "alice@example.com" in lst
    assert "bob@test.org" in lst


def test_cors_origins_list_parsed():
    s = _reload_settings(_dev_env(CORS_ORIGINS="https://app.com, https://admin.app.com"))
    lst = s.cors_origins_list
    assert "https://app.com" in lst
    assert "https://admin.app.com" in lst
