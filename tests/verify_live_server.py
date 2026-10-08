import sys
import os
sys.path.insert(0, os.path.abspath("."))

import threading
import time
import requests
import base64
import uvicorn
from app.main import app

def start_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=8009, log_level="warning")
    server = uvicorn.Server(config)
    server.run()

def run_tests():
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(2)

    base_url = "http://127.0.0.1:8009"

    # [1] Health Check
    r = requests.get(f"{base_url}/health")
    print(f"[1] Health Check: {r.status_code} - {r.json()['status']}")
    assert r.status_code == 200

    # [2] Home Page
    r = requests.get(f"{base_url}/")
    print(f"[2] Home Page: {r.status_code} - Banner Found: {'Simulated data' in r.text}")
    assert r.status_code == 200
    assert "Simulated data - not a real platform" in r.text

    # [3] Marketplace Page
    r = requests.get(f"{base_url}/marketplace")
    print(f"[3] Marketplace: {r.status_code}")
    assert r.status_code == 200

    # [4] Create Link Token
    basic_auth = base64.b64encode(b"portfolio-aggregator:bb-demo-secret").decode("utf-8")
    r = requests.post(
        f"{base_url}/open/v1/link/token",
        headers={"Authorization": f"Basic {basic_auth}"},
        json={
            "client_name": "Portfolio Aggregator",
            "scopes": ["holdings", "profile", "transactions"],
            "redirect_uri": "http://localhost:3000/link-complete",
            "webhook_url": "http://localhost:8000/webhooks/bondbazaar"
        }
    )
    print(f"[4] Create Link Token: {r.status_code} - {r.json()['linkToken'][:15]}...")
    assert r.status_code == 200
    link_token = r.json()["linkToken"]

    # [5] Authorize Link Token
    from app.database import SessionLocal
    from app.models.entities import LinkToken, User
    pub_key = f"pub_thread_test_{int(time.time()*1000)}"
    db = SessionLocal()
    lt = db.query(LinkToken).filter(LinkToken.link_token == link_token).first()
    u = db.query(User).filter(User.email == "aarav.mehta@example.com").first()
    lt.user_id = u.id
    lt.public_token = pub_key
    db.commit()
    db.close()

    # [6] Exchange Public Token
    r = requests.post(
        f"{base_url}/open/v1/link/exchange",
        headers={"Authorization": f"Basic {basic_auth}"},
        json={"publicToken": pub_key}
    )
    print(f"[5] Exchange Token: {r.status_code} - Customer: {r.json().get('customerId')}")
    assert r.status_code == 200
    access_token = r.json()["accessToken"]
    consent_id = r.json()["consentId"]

    # [7] Fetch Holdings
    r = requests.get(
        f"{base_url}/open/v1/holdings",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    print(f"[6] Fetch Holdings: {r.status_code} - Items: {len(r.json()['data'])}")
    assert r.status_code == 200
    first_holding = r.json()["data"][0]["attributes"]
    print(f"    Sample: {first_holding['instrumentName']}, Val: {first_holding['currentValuePaise']} paise, YTM: {first_holding['ytmBps']} bps")

    # [8] Revoke Consent & Confirm 403
    r = requests.post(
        f"{base_url}/open/v1/consent/revoke",
        json={"consentId": consent_id}
    )
    print(f"[7] Revoke Consent: {r.status_code}")
    assert r.status_code == 200

    r = requests.get(
        f"{base_url}/open/v1/holdings",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    print(f"[8] Access after Revocation (Expected 403): {r.status_code} - {r.json()['errors'][0]['code']}")
    assert r.status_code == 403
    assert r.json()["errors"][0]["code"] == "CONSENT_REVOKED"

    print("\n[SUCCESS] ALL LIVE HTTP SERVER ENDPOINTS VERIFIED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
