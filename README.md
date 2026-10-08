# BondBazaar (Sandbox Debt Capital & Bond Investment Platform)

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.14-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-gold.svg)](LICENSE)

**BondBazaar** is a simulated Indian Online Bond Platform Provider (OBPP) built as an institutional-grade sandbox data provider for portfolio aggregators and wealth tech systems. It emulates the workflow, bond mathematics, and trading features of platforms like GoldenPi, IndiaBonds, and Wint Wealth while operating entirely on synthetic, simulated data.

---

## 🏛️ Key Highlights & Design Identity
- **Visual Aesthetic:** Deep Navy (`#060B18`, `#0F1B38`) and Rich Gold (`#D4AF37`) "private banking" theme with serif headings, glassmorphic cards, light/dark mode, and Indian number formatting (Lakhs & Crores / ₹).
- **Exact Bond Pricing Engine:** Clean and dirty prices derived mathematically from yield-to-maturity (YTM) with Actual/365 day count convention, modified duration, and cash flow schedules.
- **Plaid-Style Link Token Flow:** External apps connect using a link token flow (`lnk_...` → user consent → `pub_...` → `acc_...`) instead of an OAuth redirect or refresh-token flow.
- **JSON:API Data Format:** Money represented in **integer paise** (never floats), yields in **basis points (bps)**, dates as `YYYY-MM-DD`, and timestamps in ISO 8601 with Indian Standard Time (`+05:30`).
- **Real-Time Webhooks:** Signed with `HMAC-SHA256` (`X-BB-Signature`) dispatched on trade settlement, coupon payouts, and principal maturities with automatic 3-attempt backoff retries.
- **Sandbox Controls:** Password-protected `/admin` console with rate shock simulation (+/- basis points), issuer downgrades, trade simulations, coupon payouts, and 503 outage / slow-mode simulators.

---

## 🚀 Quickstart

### 1. Installation
Clone the repository and install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Run the Platform
Start the server with a single command:
```bash
uvicorn app.main:app --reload
```
The application will launch at:
- **Web UI & Investor Portal:** [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Simulator & Admin Console:** [http://localhost:8000/admin](http://localhost:8000/admin)
- **System Health Check:** [http://localhost:8000/health](http://localhost:8000/health)

---

## 🔑 Demo Credentials

| Role | Identifier / Email | Password / OTP | Description |
| :--- | :--- | :--- | :--- |
| **Demo Investor 1** | `aarav.mehta@example.com` | `123456` (Demo OTP) | Aarav Mehta (`BB-77031`), 8 holdings (G-Sec, SGB, Corp, Tax-Free, T-Bill) with coupons due in 30 days. |
| **Demo Investor 2** | `priya.nair@example.com` | `123456` (Demo OTP) | Priya Nair (`BB-88142`), 6 corporate-heavy holdings including holding rated 'A' triggering risk alerts. |
| **Admin Console** | `/admin` | `admin123` | Control benchmark rate shocks, outage simulation, trades, and webhooks. |
| **Pre-Registered App** | Client ID: `portfolio-aggregator` | Secret: `bb-demo-secret` | Pre-registered third-party aggregator application. |

---

## 🔗 Plaid-Style Link Token Flow (Step-by-Step with `curl`)

### Step 1: External App Requests Link Token
The aggregator backend issues a Basic Auth request:
```bash
curl -X POST http://localhost:8000/open/v1/link/token \
  -u "portfolio-aggregator:bb-demo-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "client_name": "Portfolio Aggregator",
    "scopes": ["holdings", "profile", "transactions"],
    "redirect_uri": "http://localhost:3000/link-complete",
    "webhook_url": "http://localhost:8000/webhooks/bondbazaar"
  }'
```
**Response:**
```json
{
  "linkToken": "lnk_92f8a12e...",
  "expiresAt": "2026-10-07T16:30:00+05:30",
  "linkUrl": "http://localhost:8000/link?token=lnk_92f8a12e..."
}
```

### Step 2: User Completes Consent
The user opens `linkUrl` in their browser, enters their email + OTP (`123456`), reviews requested scopes (holdings, profile, transactions), and clicks **Allow Access**.

### Step 3: Redirection with Public Token
BondBazaar redirects the user back to the aggregator's registered redirect URI:
```
http://localhost:3000/link-complete?public_token=pub_839a11...&link_session=lsess_...
```

### Step 4: Exchange Public Token for Access Token
The aggregator backend exchanges the short-lived `public_token` for a long-lived access token:
```bash
curl -X POST http://localhost:8000/open/v1/link/exchange \
  -u "portfolio-aggregator:bb-demo-secret" \
  -H "Content-Type: application/json" \
  -d '{"publicToken": "pub_839a11..."}'
```
**Response:**
```json
{
  "accessToken": "acc_77e0c4b3...",
  "consentId": "cns_9182ab3c...",
  "consentExpiresAt": "2027-01-05T16:00:00+05:30",
  "customerId": "BB-77031"
}
```

### Step 5: Fetch Holdings & Data (Bearer Token)
The aggregator queries read-only endpoints with `Authorization: Bearer acc_...`:
```bash
curl -X GET http://localhost:8000/open/v1/holdings \
  -H "Authorization: Bearer acc_77e0c4b3..."
```
**Sample JSON:API Response:**
```json
{
  "data": [
    {
      "type": "holding",
      "id": "hld_a001",
      "attributes": {
        "isin": "IN0002000017",
        "instrumentName": "7.18% GS 2033",
        "instrumentType": "GSEC",
        "units": 50,
        "faceValuePaise": 100000,
        "avgPurchasePricePaise": 100520,
        "currentPricePaise": 100840,
        "investedPaise": 5026000,
        "currentValuePaise": 5042000,
        "accruedInterestPaise": 53500,
        "couponRateBps": 718,
        "ytmBps": 708,
        "couponFrequency": "SEMI_ANNUAL",
        "nextCouponDate": "2027-02-14",
        "maturityDate": "2033-08-14",
        "rating": {
          "agency": "Reserve Bank of India",
          "grade": "SOVEREIGN"
        }
      },
      "relationships": {
        "demat": {
          "dpName": "BondBazaar Depository Services",
          "accountMasked": "12081600****3308"
        }
      }
    }
  ],
  "links": {
    "self": "/open/v1/holdings?limit=20",
    "next": null
  },
  "meta": {
    "generatedAt": "2026-10-07T15:30:00+05:30",
    "count": 8
  }
}
```

### Step 6: Revoke Consent
```bash
curl -X POST http://localhost:8000/open/v1/consent/revoke \
  -H "Content-Type: application/json" \
  -d '{"consentId": "cns_9182ab3c..."}'
```
Subsequent requests with that token immediately return `HTTP 403`:
```json
{
  "errors": [
    {
      "code": "CONSENT_REVOKED",
      "detail": "Consent was revoked by the user."
    }
  ]
}
```

---

## 🔔 Webhook Signatures & Verification

When holdings update (trade executed, coupon credited, bond matured), BondBazaar posts a payload to `webhook_url`:
```json
{
  "event": "HOLDINGS_UPDATED",
  "consentId": "cns_9182ab3c...",
  "customerId": "BB-77031",
  "occurredAt": "2026-10-07T15:45:00+05:30"
}
```
Each request includes the HMAC signature in header `X-BB-Signature`:
```python
import hmac, hashlib

expected_sig = hmac.new(
    WEBHOOK_SIGNING_SECRET.encode("utf-8"),
    raw_request_body,
    hashlib.sha256
).hexdigest()

is_valid = hmac.compare_digest(expected_sig, received_header)
```

Run the included standalone sample receiver:
```bash
python webhook_receiver_sample.py
```

---

## 🧮 Bond Math Engine Details
- **Dirty Price:** Discounted sum of all future cash flows (coupons + principal):
  $$PV = \sum_{t=1}^{n} \frac{CF_t}{(1 + y/m)^{m \cdot t}}$$
- **Accrued Interest:** Actual/365 convention:
  $$AI = F \times c \times \frac{\text{Days since last coupon}}{365}$$
- **Clean Price:** $\text{Clean Price} = \text{Dirty Price} - AI$
- **Modified Duration:**
  $$D_{\text{mod}} = \frac{D_{\text{Mac}}}{1 + y/m}$$
- **Rate Shocks:** Benchmark shifts move prices dynamically ($\Delta P / P \approx -D_{\text{mod}} \times \Delta y$).

---

## 🧪 Running Unit & Integration Tests
Execute all 14 tests:
```bash
python -m pytest -v
```

---

## 🐳 Docker & Render Deployment
Deploy via Docker:
```bash
docker build -t bondbazaar .
docker run -p 8000:8000 -e PLATFORM_NAME="BondBazaar" bondbazaar
```
Deploy to Render:
- Service Type: **Web Service**
- Environment: **Python 3**
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
