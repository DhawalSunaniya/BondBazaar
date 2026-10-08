"""
shared_identity.py — Deterministic identity generation for BondBazaar (Provider C).

Rules:
- normalize_email: lowercase + strip whitespace. Same email = same normalized form everywhere.
- generate_identity: HMAC-SHA256 keyed on SHARED_IDENTITY_SALT. Deterministic: same
  normalized email always produces the same output across all 4 provider projects.
- Full name from Google name claim when available; otherwise email local-part (capitalised).
- Never generate real-looking Aadhaar or PAN numbers (no 12-digit or standard PAN format).
"""

import hashlib
import hmac
import re
import secrets
from typing import Optional

from app.config import settings

# ── helpers ──────────────────────────────────────────────────────────────────

def normalize_email(email: str) -> str:
    """Lowercase + strip every kind of whitespace."""
    return email.strip().lower()


def generate_identity(email: str) -> dict:
    """
    Returns a deterministic fake identity dict keyed on the normalised email.
    Uses HMAC-SHA256 with SHARED_IDENTITY_SALT so the same email always yields
    the same customer_id, demat, mobile, etc. across all four providers.

    Fields that must NOT look like real Aadhaar/PAN are intentionally formatted
    to be clearly fictitious (e.g. demat uses provider-prefix "IN300003-BB-…").
    """
    salt = getattr(settings, "SHARED_IDENTITY_SALT", "tradeone-shared-identity-salt-2026")
    norm = normalize_email(email)

    def _derive(tag: str, length: int = 8) -> str:
        """HMAC-SHA256 sub-derivation; returns hex digits truncated to `length`."""
        mac = hmac.new(
            salt.encode("utf-8"),
            f"{tag}:{norm}".encode("utf-8"),
            hashlib.sha256,
        )
        return mac.hexdigest()[:length]

    # Customer ID: BB-NNNNN  (5 decimal digits, provider-C prefix)
    cid_hex = _derive("customer_id", 5)
    cid_num = int(cid_hex, 16) % 90000 + 10000
    customer_id = f"BB-{cid_num}"

    # Demat: IN300003-BB-XXXXXXXX  — clearly fictional, not a real CDSL/NSDL format
    demat_raw = _derive("demat", 8).upper()
    demat_account = f"IN300003-BB-{demat_raw}"
    demat_masked = f"IN300003-BB-****{demat_raw[-4:]}"

    # Mobile: +91 9XXXXXXX (8 hex digits deterministically → 8 decimal digits)
    mob_num = int(_derive("mobile", 8), 16) % 100000000
    mobile = f"+91 9{mob_num:08d}"

    # Fake PAN-like string — NOT a real PAN format (uses provider prefix "BB" to mark it)
    # Real PAN: AAAAA9999A — we deliberately break that pattern
    pan_raw = _derive("pan", 6).upper()
    pan_code = f"BB{pan_raw[:6]}X"          # 9 chars, not 10, clearly not real PAN
    pan_masked = f"BB{pan_raw[:2]}****X"

    return {
        "customer_id": customer_id,
        "demat_account": demat_account,
        "demat_account_masked": demat_masked,
        "mobile": mobile,
        "pan": pan_code,
        "pan_masked": pan_masked,
        "bank_name": "BondBazaar Payments Bank",
        "bank_account_masked": f"XXXX{_derive('bank', 4).upper()}",
        "bank_ifsc": f"BBPB{_derive('ifsc', 7).upper()[:7]}",
    }


def full_name_from_claims(google_name: Optional[str], email: str) -> str:
    """
    Use Google name claim when present; otherwise derive a display name from the
    email local-part (e.g. "john.doe" → "John Doe").
    """
    if google_name and google_name.strip():
        return google_name.strip()
    local = normalize_email(email).split("@")[0]
    # Replace separators with spaces and title-case
    name = re.sub(r"[._+\-]+", " ", local).title()
    return name or "BondBazaar User"
