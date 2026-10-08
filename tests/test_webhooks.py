import json
import pytest
from app.services.webhook_service import compute_hmac_signature, verify_hmac_signature

def test_webhook_hmac_signature():
    secret = "bb_whsec_secret_key_demo_8839"
    payload = json.dumps({
        "event": "HOLDINGS_UPDATED",
        "consentId": "cns_9a8b7c6d",
        "customerId": "BB-77031",
        "occurredAt": "2026-10-07T16:00:00+05:30"
    })
    payload_bytes = payload.encode("utf-8")

    # Generate signature
    signature = compute_hmac_signature(secret, payload_bytes)
    assert len(signature) == 64 # 32 bytes hex

    # Verify signature
    assert verify_hmac_signature(secret, payload_bytes, signature) is True
    assert verify_hmac_signature(secret, payload_bytes, f"sha256={signature}") is True

    # Tampered payload must fail
    tampered_bytes = payload.replace("BB-77031", "BB-99999").encode("utf-8")
    assert verify_hmac_signature(secret, tampered_bytes, signature) is False

    # Wrong secret must fail
    assert verify_hmac_signature("wrong_secret", payload_bytes, signature) is False
