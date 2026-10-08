"""
BondBazaar Sample Webhook Receiver
==================================
Demonstrates how third-party portfolio aggregators receive and verify
real-time HMAC-SHA256 signed event notifications from BondBazaar.

Run this script:
    python webhook_receiver_sample.py
"""

import hmac
import hashlib
from fastapi import FastAPI, Request, Header, HTTPException, status
import uvicorn

WEBHOOK_SIGNING_SECRET = "bb_whsec_secret_key_demo_8839"

app = FastAPI(title="Portfolio Aggregator Webhook Receiver")

def verify_signature(payload_bytes: bytes, signature_header: str) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(
        WEBHOOK_SIGNING_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    clean_sig = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, clean_sig)

@app.post("/webhooks/bondbazaar")
async def receive_bondbazaar_webhook(request: Request, x_bb_signature: str = Header(None)):
    body = await request.body()
    
    # 1. Verify HMAC-SHA256 signature
    if not verify_signature(body, x_bb_signature):
        print(f"[REJECTED] Invalid signature received: {x_bb_signature}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC-SHA256 signature"
        )

    # 2. Process event payload
    event_data = await request.json()
    print("\n=======================================================")
    print("🔔 RECEIVED VERIFIED BONDBAZAAR WEBHOOK EVENT:")
    print(f"Event Type : {event_data.get('event')}")
    print(f"Customer ID: {event_data.get('customerId')}")
    print(f"Consent ID : {event_data.get('consentId')}")
    print(f"Occurred At: {event_data.get('occurredAt')}")
    print(f"Signature  : {x_bb_signature[:16]}... (Valid)")
    print("=======================================================\n")

    # In production: enqueue job to refresh portfolio holdings for this customer
    return {"status": "ACKNOWLEDGED", "event": event_data.get("event")}

if __name__ == "__main__":
    print(f"Starting Webhook Receiver on http://localhost:8000/webhooks/bondbazaar...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
