import base64
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models.entities import User, Instrument, LinkToken, ApiConsent, WebhookLog
from app.services.webhook_service import verify_hmac_signature
from app.config import settings

def test_full_e2e_journey():
    client = TestClient(app)

    # 1. Health check
    res = client.get("/health")
    assert res.status_code == 200

    # 2. Step 1: Create Link Token (Basic Auth)
    basic_auth = base64.b64encode(b"portfolio-aggregator:bb-demo-secret").decode("utf-8")
    token_resp = client.post(
        "/open/v1/link/token",
        headers={"Authorization": f"Basic {basic_auth}"},
        json={
            "client_name": "Portfolio Aggregator",
            "scopes": ["holdings", "profile", "transactions"],
            "redirect_uri": "http://localhost:3000/link-complete",
            "webhook_url": "http://localhost:8000/webhooks/bondbazaar"
        }
    )
    assert token_resp.status_code == 200, token_resp.text
    token_json = token_resp.json()
    link_token = token_json["linkToken"]
    assert link_token.startswith("lnk_")

    # 3. Step 2: Complete Consent
    # Sign in as Aarav Mehta via OTP
    otp_resp = client.post("/link/verify-login", data={
        "token": link_token,
        "email": "aarav.mehta@example.com",
        "otp": "123456"
    })
    assert otp_resp.status_code == 200

    # User clicks Allow
    allow_resp = client.post("/link/authorize", data={
        "token": link_token,
        "action": "allow"
    }, cookies=otp_resp.cookies)
    assert allow_resp.status_code == 200

    # Extract public token
    db = SessionLocal()
    lt = db.query(LinkToken).filter(LinkToken.link_token == link_token).first()
    public_token = lt.public_token
    db.close()
    assert public_token.startswith("pub_")

    # 4. Step 3: Exchange Public Token
    exchange_resp = client.post(
        "/open/v1/link/exchange",
        headers={"Authorization": f"Basic {basic_auth}"},
        json={"publicToken": public_token}
    )
    assert exchange_resp.status_code == 200
    exchange_data = exchange_resp.json()
    access_token = exchange_data["accessToken"]
    consent_id = exchange_data["consentId"]
    customer_id = exchange_data["customerId"]
    assert customer_id == "BB-77031"
    assert access_token.startswith("acc_")

    # 5. Step 4: Fetch Holdings via Bearer Token
    bearer_hdr = {"Authorization": f"Bearer {access_token}"}
    holdings_resp = client.get("/open/v1/holdings", headers=bearer_hdr)
    assert holdings_resp.status_code == 200
    h_data = holdings_resp.json()
    assert len(h_data["data"]) == 8 # Aarav has 8 seeded holdings
    # Verify JSON:API integer paise shape
    first_holding = h_data["data"][0]["attributes"]
    assert isinstance(first_holding["faceValuePaise"], int)
    assert isinstance(first_holding["currentPricePaise"], int)
    assert isinstance(first_holding["investedPaise"], int)
    assert isinstance(first_holding["couponRateBps"], int)

    # 6. Step 5: Trigger Trade from Admin and Confirm Webhook Generated
    admin_login_resp = client.post("/admin/login", data={"password": settings.ADMIN_PASSWORD}, follow_redirects=False)
    assert admin_login_resp.status_code == 303

    db = SessionLocal()
    user = db.query(User).filter(User.customer_id == customer_id).first()
    inst = db.query(Instrument).filter(Instrument.isin == first_holding["isin"]).first()
    prev_logs_count = db.query(WebhookLog).filter(WebhookLog.consent_id == consent_id).count()
    db.close()

    sim_resp = client.post("/admin/simulate-trade", data={
        "user_id": user.id,
        "instrument_id": inst.id,
        "side": "BUY",
        "units": inst.min_lot_size
    }, cookies=admin_login_resp.cookies, follow_redirects=False)
    assert sim_resp.status_code == 303

    # Check that a webhook log was created with valid HMAC signature
    db = SessionLocal()
    new_logs = db.query(WebhookLog).filter(WebhookLog.consent_id == consent_id).all()
    db.close()
    assert len(new_logs) > prev_logs_count
    latest_wh = new_logs[-1]
    assert latest_wh.event == "HOLDINGS_UPDATED"
    assert latest_wh.customer_id == customer_id
    payload_dict = json.loads(latest_wh.payload)
    assert payload_dict["event"] == "HOLDINGS_UPDATED"

    # Verify signature
    from app.services.webhook_service import compute_hmac_signature
    sig = compute_hmac_signature(settings.WEBHOOK_SIGNING_SECRET, latest_wh.payload.encode("utf-8"))
    assert verify_hmac_signature(settings.WEBHOOK_SIGNING_SECRET, latest_wh.payload.encode("utf-8"), sig) is True

    # 7. Step 6: Revoke Consent and Confirm 403
    revoke_resp = client.post("/open/v1/consent/revoke", json={"consentId": consent_id})
    assert revoke_resp.status_code == 200

    # Subsequent data call must return HTTP 403
    blocked_resp = client.get("/open/v1/holdings", headers=bearer_hdr)
    assert blocked_resp.status_code == 403
    err_body = blocked_resp.json()
    assert err_body["errors"][0]["code"] == "CONSENT_REVOKED"
