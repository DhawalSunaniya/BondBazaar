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

---

## 🔌 TradeOne Internal API Integration

BondBazaar integrates with the TradeOne portfolio aggregator as **Provider C** (theme: Government Securities, G-Secs, and SGBs).

### Configuration

| Variable | Description | Default |
|---|---|---|
| `BROKER_NAME` | Broker identity | `BondBazaar` |
| `PROVIDER_CODE` | Provider single-character code | `c` |
| `DP_NAME` | Depository participant name | `BondBazaar Depository Services` |
| `DP_ID` | Depository participant ID | `IN300003` |
| `INTERNAL_API_ENABLED` | Enable internal TradeOne endpoints | `false` |
| `INTERNAL_API_KEY` | Secret key required in `x-internal-key` header | `""` |
| `SHARED_IDENTITY_SALT` | Salt for deterministic cross-broker HMAC identity | `tradeone-shared-identity-salt-2026` |
| `TRADEONE_URL` | Base URL of TradeOne aggregator outbox destination | `""` |

### Security & Authentication

- Guarded by `INTERNAL_API_ENABLED=true`.
- Requires header `x-internal-key` verified via constant-time HMAC comparison (`hmac.compare_digest`).
- Missing or invalid key immediately returns `HTTP 401`. The key is **never logged**.
- Built-in rate limiting: **60 requests/minute** per IP (returns `HTTP 429` with `Retry-After: 60`).

### Endpoints

#### 1. Provision User
```bash
POST /internal/v1/users/provision
Content-Type: application/json
x-internal-key: <INTERNAL_API_KEY>

{
  "email": "user@example.com",
  "fullName": "Aarav Sharma"
}
```
*Idempotent*: returns existing user or deterministically creates user and assigns starter portfolio (G-Secs, SGBs, with 2-3 cross-broker overlapping ISINs, prices within ±15% of market price, and starting wallet ₹10,00,000).

#### 2. User Profile
```bash
GET /internal/v1/users/{email}/profile
x-internal-key: <INTERNAL_API_KEY>
```
Returns profile matching `GET /open/v1/customer` structure.

#### 3. Holdings Parity
```bash
GET /internal/v1/users/{email}/holdings?limit=20&cursor=<holding_id>
x-internal-key: <INTERNAL_API_KEY>
```
Returns holdings in **identical JSON:API structure and pagination** (`data`, `links`, `meta`) as the public `GET /open/v1/holdings`.

#### 4. Portfolio Summary
```bash
GET /internal/v1/users/{email}/summary
x-internal-key: <INTERNAL_API_KEY>
```
Returns aggregate totals (`totalInvestedPaise`, `totalCurrentValuePaise`, `totalAccruedInterestPaise`, `unrealisedPnlPaise`, `walletBalancePaise`, `holdingCount`).

### Outbox Event Notification

After any committed holdings change (secondary market order settlement, bond maturity redemption), an outbox event is enqueued asynchronously and posted to:
```
POST {TRADEONE_URL}/internal/v1/events
```
Body:
```json
{
  "provider": "c",
  "email": "user@example.com",
  "event": "HOLDINGS_CHANGED",
  "occurredAt": "2026-10-09T01:30:00+05:30"
}
```
- Retries up to **10 times with exponential backoff** (1s to 60s).
- Fire-and-forget: **never blocks or fails trades**.

---

## ⚙️ Configuration

BondBazaar is configured via environment variables (or a local `.env` file loaded via `pydantic-settings`).

> [!IMPORTANT]
> **Render does not read `.env` files!**
> When deploying to Render, you must configure all environment variables directly in the Render Dashboard under **Dashboard → Service → Environment Variables**. The `.env` file is ignored by Git and only applies to local development.

### Setup Helper
To quickly configure a local development environment with fresh cryptographic secrets:
```bash
python scripts/setup_env.py
```
This copies `.env.example` to `.env` (without overwriting an existing one), generates per-site secrets using `secrets.token_urlsafe(32)`, and prompts for the shared secrets (`SHARED_IDENTITY_SALT` and `INTERNAL_API_KEY`) that must match your sibling sites.

### Environment Variables Reference

| Variable | Required in Production | Default | Purpose |
| :--- | :---: | :--- | :--- |
| `ENVIRONMENT` | No | `development` | Environment mode (`development` or `production`). In production, missing or weak secrets cause the server to refuse startup. |
| `SECRET_KEY` | **Yes** | Auto-generated in dev | Cryptographic signing key for JWTs and auth tokens. |
| `SESSION_SECRET` | **Yes** | Auto-generated in dev | Cryptographic secret for browser session cookies. |
| `ADMIN_PASSWORD` | **Yes** | Auto-generated in dev | Password for the `/admin` simulation and control portal. |
| `WEBHOOK_SIGNING_SECRET` | **Yes** | Auto-generated in dev | HMAC-SHA256 secret for outbound webhook signatures (`X-BB-Signature`). |
| `INTERNAL_API_KEY` | **Yes** *(if internal API enabled)* | `None` | Pre-shared key for TradeOne aggregator (`x-internal-key` header). **Must be identical across all sibling sites.** |
| `SHARED_IDENTITY_SALT` | **Yes** *(if internal API enabled)* | `None` | Salt for deterministic PAN, Demat, and Customer ID generation. **Must be identical across all sibling sites.** |
| `INTERNAL_API_ENABLED` | No | `false` | Enables `/internal/v1/*` endpoints for TradeOne integration. Automatically forced to `false` if shared secrets are missing. |
| `DATABASE_URL` | No | `sqlite:///./bondbazaar.db` | Database connection URL. Legacy `postgres://` URLs are automatically converted to `postgresql://`. |
| `BASE_URL` | No | `http://localhost:8000` | Canonical public URL for BondBazaar (used in Link token redirect URLs). |
| `TRADEONE_URL` | No | `http://localhost:3000` | Base URL of the TradeOne aggregator (receives outbox events). |
| `DEPOSITORY_URL` | No | `None` | External clearing / depository sandbox URL (optional). |
| `GOOGLE_CLIENT_ID` | No | `None` | OAuth2 Client ID for Google Single Sign-On. |
| `GOOGLE_CLIENT_SECRET` | No | `None` | OAuth2 Client Secret for Google Single Sign-On. |
| `GOOGLE_REDIRECT_URI` | No | `None` | Redirect callback URL for Google OAuth2 flow. |
| `ALLOWED_REDIRECT_URIS` | No | `http://localhost:3000/link-complete` | Comma-separated list of allowed Link flow callback URLs. |
| `ALLOWED_WEBHOOK_URLS` | No | `http://localhost:8000/webhooks/bondbazaar` | Comma-separated list of allowed client webhook destinations. |
| `ALLOWED_EMAILS` | No | `None` | Comma-separated list of permitted emails; if set, login/signup is restricted. |
| `CORS_ORIGINS` | No | `*` | Comma-separated list of allowed CORS origins. |
| `STARTING_FUNDS` | No | `1000000.0` | Default wallet balance in ₹ (Rupees) for new users (₹10 Lakh). |
| `SEED_STARTER_PORTFOLIO` | No | `false` | Automatically allocate sample bond holdings on user signup. |
| `DEMO_OTP` | No | `123456` | Fixed OTP for testing/development. |
| `DEMO_API_CLIENT_SECRET` | No | `bb-demo-secret` (dev only) | Seeded secret for demo `portfolio-aggregator` API client. |
| `PLATFORM_NAME` | No | `BondBazaar` | Platform display name in UI headers and emails. |
| `PROVIDER_CODE` | No | `c` | Provider code in TradeOne ecosystem (`c` = BondBazaar). |
| `DP_NAME` | No | `BondBazaar Depository Services Ltd` | Depository Participant name. |
| `DP_ID` | No | `IN303892` | Depository Participant ID. |

---

## 🧪 Running Unit & Integration Tests
Execute all unit and integration tests:
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

