import base64
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "UP"
    assert data["platform"] == "BondBazaar"

def test_open_api_e2e_flow():
    # 1. Create Link Token with Basic Auth
    basic_creds = base64.b64encode(b"portfolio-aggregator:bb-demo-secret").decode("utf-8")
    resp = client.post(
        "/open/v1/link/token",
        headers={"Authorization": f"Basic {basic_creds}"},
        json={
            "client_name": "Portfolio Aggregator",
            "scopes": ["holdings", "profile", "transactions"],
            "redirect_uri": "http://localhost:3000/link-complete",
            "webhook_url": "http://localhost:8000/webhooks/bondbazaar"
        }
    )
    assert resp.status_code == 200, resp.text
    token_data = resp.json()
    assert "linkToken" in token_data
    assert "linkUrl" in token_data
    link_token = token_data["linkToken"]

    # 2. Authorize via link page
    # First sign in as Aarav Mehta
    otp_resp = client.post("/link/verify-login", data={
        "token": link_token,
        "email": "aarav.mehta@example.com",
        "otp": "123456"
    })
    assert otp_resp.status_code == 200

    # User clicks Allow
    auth_resp = client.post(
        "/link/authorize",
        data={"token": link_token, "action": "allow"},
        cookies=otp_resp.cookies
    )
    assert auth_resp.status_code == 200
    # In auth response HTML, we have the redirect URL with public_token
    # Let's extract public token from LinkToken in DB or exchange directly
    from app.database import SessionLocal
    from app.models.entities import LinkToken
    db = SessionLocal()
    token_rec = db.query(LinkToken).filter(LinkToken.link_token == link_token).first()
    pub_token = token_rec.public_token
    db.close()
    assert pub_token is not None

    # 3. Exchange public token
    exchange_resp = client.post(
        "/open/v1/link/exchange",
        headers={"Authorization": f"Basic {basic_creds}"},
        json={"publicToken": pub_token}
    )
    assert exchange_resp.status_code == 200, exchange_resp.text
    access_data = exchange_resp.json()
    assert "accessToken" in access_data
    assert "consentId" in access_data
    assert access_data["customerId"] == "BB-77031"
    access_token = access_data["accessToken"]
    consent_id = access_data["consentId"]

    # 4. Fetch Customer Profile
    bearer_hdr = {"Authorization": f"Bearer {access_token}"}
    cust_resp = client.get("/open/v1/customer", headers=bearer_hdr)
    assert cust_resp.status_code == 200
    cust_data = cust_resp.json()
    assert cust_data["data"]["type"] == "customer"
    assert cust_data["data"]["id"] == "BB-77031"
    assert "maskedPan" in cust_data["data"]["attributes"]

    # 5. Fetch Holdings (Verify JSON:API shape, integer paise, basis points)
    holdings_resp = client.get("/open/v1/holdings?limit=20", headers=bearer_hdr)
    assert holdings_resp.status_code == 200
    h_data = holdings_resp.json()
    assert "data" in h_data
    assert "meta" in h_data
    assert len(h_data["data"]) > 0

    first_h = h_data["data"][0]
    assert first_h["type"] == "holding"
    assert "attributes" in first_h
    attrs = first_h["attributes"]
    # Check money as integer paise (never float)
    assert isinstance(attrs["faceValuePaise"], int)
    assert isinstance(attrs["avgPurchasePricePaise"], int)
    assert isinstance(attrs["currentPricePaise"], int)
    assert isinstance(attrs["investedPaise"], int)
    assert isinstance(attrs["currentValuePaise"], int)
    assert isinstance(attrs["accruedInterestPaise"], int)
    # Check yields in basis points
    assert isinstance(attrs["couponRateBps"], int)
    assert isinstance(attrs["ytmBps"], int)
    # Check ISO date
    assert len(attrs["maturityDate"]) == 10 # YYYY-MM-DD

    # 6. Fetch Instruments
    inst_resp = client.get("/open/v1/instruments?type=CORPORATE_BOND", headers=bearer_hdr)
    assert inst_resp.status_code == 200
    inst_data = inst_resp.json()
    assert len(inst_data["data"]) > 0
    assert inst_data["data"][0]["type"] == "instrument"

    # 7. Revoke consent
    revoke_resp = client.post("/open/v1/consent/revoke", json={"consentId": consent_id})
    assert revoke_resp.status_code == 200

    # 8. Subsequent data call must return HTTP 403 with CONSENT_REVOKED
    blocked_resp = client.get("/open/v1/holdings", headers=bearer_hdr)
    assert blocked_resp.status_code == 403
    err_json = blocked_resp.json()
    assert "errors" in err_json
    assert err_json["errors"][0]["code"] == "CONSENT_REVOKED"
