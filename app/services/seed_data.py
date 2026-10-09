import hashlib
import json
import os
import random
import datetime
from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.models.entities import (
    User, Instrument, PriceHistory, Holding, SecondaryOrder,
    WalletTransaction, PrimaryIssue, PrimaryApplication,
    ApiClient, LinkToken, ApiConsent, WebhookLog, ApiCallLog, SystemState
)
from app.services.bond_math import calculate_bond_metrics
from app.config import settings

def _hash_secret(secret: str) -> str:
    """SHA-256 hash for API client secrets. Matches auth_service.verify_secret()."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()

SHARED_INSTRUMENTS_PATHS = [
    "instruments_shared.json",
    os.path.join("..", "instruments_shared.json")
]

RAW_INSTRUMENTS_SPEC = [
    # G-Secs (Government Securities)
    {
        "name": "7.18% GS 2033",
        "issuer_name": "Government of India",
        "instrument_type": "GSEC",
        "face_value": 1000.0,
        "coupon_rate": 0.0718,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2023-08-14",
        "maturity_date": "2033-08-14",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 10.0,
        "explainer": "Central Government dated security backed by the sovereign guarantee of the Government of India. Half-yearly coupon payouts.",
        "risk_profile": "Negligible credit risk. Moderate interest rate risk given ~7-year duration. High liquidity in secondary market."
    },
    {
        "name": "7.10% GS 2034",
        "issuer_name": "Government of India",
        "instrument_type": "GSEC",
        "face_value": 1000.0,
        "coupon_rate": 0.0710,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2024-04-08",
        "maturity_date": "2034-04-08",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 2.0,
        "explainer": "Current 10-Year Benchmark G-Sec of India. Highest secondary market trading volume.",
        "risk_profile": "Zero default risk. Subject to sovereign yield curve shifts. Deep liquidity."
    },
    {
        "name": "7.26% GS 2032",
        "issuer_name": "Government of India",
        "instrument_type": "GSEC",
        "face_value": 1000.0,
        "coupon_rate": 0.0726,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2022-08-22",
        "maturity_date": "2032-08-22",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 14.0,
        "explainer": "Medium-term sovereign bond offering semi-annual coupons.",
        "risk_profile": "Sovereign guaranteed. Price fluctuates inversely with benchmark yields."
    },
    {
        "name": "7.38% GS 2027",
        "issuer_name": "Government of India",
        "instrument_type": "GSEC",
        "face_value": 1000.0,
        "coupon_rate": 0.0738,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2022-06-20",
        "maturity_date": "2027-06-20",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": -8.0,
        "explainer": "Short to medium-term sovereign paper maturing in 2027.",
        "risk_profile": "Very low duration risk, sovereign backed, excellent stability."
    },
    {
        "name": "6.54% GS 2032",
        "issuer_name": "Government of India",
        "instrument_type": "GSEC",
        "face_value": 1000.0,
        "coupon_rate": 0.0654,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2022-01-17",
        "maturity_date": "2032-01-17",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 12.0,
        "explainer": "Discounted G-Sec issued during lower rate environment, trading below par.",
        "risk_profile": "Zero credit risk. High sensitivity to long term interest rates."
    },
    {
        "name": "7.29% GS 2033",
        "issuer_name": "Government of India",
        "instrument_type": "GSEC",
        "face_value": 1000.0,
        "coupon_rate": 0.0729,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2023-05-15",
        "maturity_date": "2033-05-15",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 8.0,
        "explainer": "Sovereign bond with coupons due in May and November.",
        "risk_profile": "Zero default risk. Moderate interest rate duration."
    },

    # T-Bills (Treasury Bills - Zero Coupon)
    {
        "name": "91 Day Treasury Bill (Nov 2026)",
        "issuer_name": "Government of India",
        "instrument_type": "TBILL",
        "face_value": 100.0,
        "coupon_rate": 0.0,
        "coupon_frequency": "CUMULATIVE",
        "issue_date": "2026-08-20",
        "maturity_date": "2026-11-19",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 100,
        "benchmark_spread_bps": -45.0,
        "explainer": "Short-term sovereign zero-coupon money market instrument issued at a discount to par (₹100).",
        "risk_profile": "Virtually risk-free. No reinvestment risk or coupon default risk."
    },
    {
        "name": "182 Day Treasury Bill (Jan 2027)",
        "issuer_name": "Government of India",
        "instrument_type": "TBILL",
        "face_value": 100.0,
        "coupon_rate": 0.0,
        "coupon_frequency": "CUMULATIVE",
        "issue_date": "2026-07-23",
        "maturity_date": "2027-01-21",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 100,
        "benchmark_spread_bps": -35.0,
        "explainer": "Six-month discounted government instrument for short-term liquidity parking.",
        "risk_profile": "Zero default risk, minimal duration volatility."
    },
    {
        "name": "364 Day Treasury Bill (Jun 2027)",
        "issuer_name": "Government of India",
        "instrument_type": "TBILL",
        "face_value": 100.0,
        "coupon_rate": 0.0,
        "coupon_frequency": "CUMULATIVE",
        "issue_date": "2026-06-18",
        "maturity_date": "2027-06-17",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 100,
        "benchmark_spread_bps": -20.0,
        "explainer": "One-year money market discount security issued by the RBI on behalf of GoI.",
        "risk_profile": "Zero credit risk, highly liquid, ideal for sub-1-year allocation."
    },

    # State Development Loans (SDLs)
    {
        "name": "7.65% Maharashtra SDL 2033",
        "issuer_name": "Government of Maharashtra",
        "instrument_type": "SDL",
        "face_value": 1000.0,
        "coupon_rate": 0.0765,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2023-09-20",
        "maturity_date": "2033-09-20",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 38.0,
        "explainer": "State Development Loan issued by Maharashtra government via RBI auction. Yields higher than central G-Secs.",
        "risk_profile": "Sovereign quality serviced via RBI consolidated sinking fund. Slightly lower liquidity than G-Sec."
    },
    {
        "name": "7.72% Tamil Nadu SDL 2034",
        "issuer_name": "Government of Tamil Nadu",
        "instrument_type": "SDL",
        "face_value": 1000.0,
        "coupon_rate": 0.0772,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2024-02-14",
        "maturity_date": "2034-02-14",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 42.0,
        "explainer": "10-year SDL offering attractive semi-annual interest, backed by state revenues.",
        "risk_profile": "High credit safety backed by RBI automatic debit mechanism. Moderate duration."
    },
    {
        "name": "7.58% Gujarat SDL 2031",
        "issuer_name": "Government of Gujarat",
        "instrument_type": "SDL",
        "face_value": 1000.0,
        "coupon_rate": 0.0758,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2021-11-10",
        "maturity_date": "2031-11-10",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 32.0,
        "explainer": "Gujarat state government security trading at a moderate premium over G-Secs.",
        "risk_profile": "Pristine sovereign track record. Moderate secondary market volume."
    },
    {
        "name": "7.69% Karnataka SDL 2032",
        "issuer_name": "Government of Karnataka",
        "instrument_type": "SDL",
        "face_value": 1000.0,
        "coupon_rate": 0.0769,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2022-07-27",
        "maturity_date": "2032-07-27",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 36.0,
        "explainer": "State security providing steady 7.69% annual cash flows distributed semi-annually.",
        "risk_profile": "Very low credit risk, institutional participation."
    },

    # Sovereign Gold Bonds (SGBs)
    {
        "name": "Sovereign Gold Bond 2021-22 Series VIII",
        "issuer_name": "Government of India",
        "instrument_type": "SGB",
        "face_value": 4791.0,
        "coupon_rate": 0.0250,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2021-11-02",
        "maturity_date": "2029-11-02",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 1,
        "benchmark_spread_bps": -380.0,
        "explainer": "Denominated in grams of gold. Pays 2.50% p.a. fixed interest plus capital appreciation linked to gold bullion prices.",
        "risk_profile": "Gold commodity price volatility, zero default risk, capital gains tax exempt on maturity."
    },
    {
        "name": "Sovereign Gold Bond 2022-23 Series II",
        "issuer_name": "Government of India",
        "instrument_type": "SGB",
        "face_value": 5197.0,
        "coupon_rate": 0.0250,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2022-08-30",
        "maturity_date": "2030-08-30",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 1,
        "benchmark_spread_bps": -380.0,
        "explainer": "Government gold bond tranche with semi-annual payout in February and August.",
        "risk_profile": "Linked to domestic gold price. Sovereign safety for principal grams and interest."
    },
    {
        "name": "Sovereign Gold Bond 2023-24 Series I",
        "issuer_name": "Government of India",
        "instrument_type": "SGB",
        "face_value": 5923.0,
        "coupon_rate": 0.0250,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2023-06-27",
        "maturity_date": "2031-06-27",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 1,
        "benchmark_spread_bps": -380.0,
        "explainer": "Gold bond issued in June 2023. Semi-annual interest credited directly to bank account.",
        "risk_profile": "Bullion price sensitivity. Sovereign backing."
    },
    {
        "name": "Sovereign Gold Bond 2023-24 Series III",
        "issuer_name": "Government of India",
        "instrument_type": "SGB",
        "face_value": 6199.0,
        "coupon_rate": 0.0250,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2023-12-28",
        "maturity_date": "2031-12-28",
        "credit_rating": "SOVEREIGN",
        "rating_agency": "Reserve Bank of India",
        "is_secured": True,
        "min_lot_size": 1,
        "benchmark_spread_bps": -380.0,
        "explainer": "Recent gold bond series with 8-year tenor and early exit option after 5th year.",
        "risk_profile": "Tracks market price of 999 purity gold. Zero default risk."
    },

    # Corporate Bonds (Fictional issuers with fictional rating agencies)
    {
        "name": "Example Infra Finance 8.10% 2031",
        "issuer_name": "Example Infra Finance Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0810,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2021-06-15",
        "maturity_date": "2031-06-15",
        "credit_rating": "AAA",
        "rating_agency": "Beacon Ratings",
        "is_secured": True,
        "min_lot_size": 5,
        "benchmark_spread_bps": 74.0,
        "explainer": "Senior secured rated debentures issued to fund renewable energy corridors. Backed by asset cover.",
        "risk_profile": "High credit quality (AAA). Low credit spread risk. Moderate sensitivity to interest rate moves."
    },
    {
        "name": "Sunrise Housing Finance 8.45% 2029",
        "issuer_name": "Sunrise Housing Finance Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0845,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2022-10-18",
        "maturity_date": "2029-10-18",
        "credit_rating": "AA+",
        "rating_agency": "IndiCredit",
        "is_secured": True,
        "min_lot_size": 5,
        "benchmark_spread_bps": 115.0,
        "explainer": "Affordable housing finance debentures secured by retail mortgage pool receivables.",
        "risk_profile": "Solid AA+ rating. Low prepayment and credit delinquency risks. Annual coupon payout."
    },
    {
        "name": "Apex Renewable Power 8.75% 2028",
        "issuer_name": "Apex Renewable Power Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0875,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2023-03-24",
        "maturity_date": "2028-03-24",
        "credit_rating": "AA",
        "rating_agency": "TrustRatings",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 150.0,
        "explainer": "Green bond tranche financing utility-scale solar and wind installations across western India.",
        "risk_profile": "AA rating. Cash flows protected by long-term power purchase agreements (PPAs)."
    },
    {
        "name": "Falcon Logistics 9.20% 2027",
        "issuer_name": "Falcon Logistics Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0920,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2022-11-05",
        "maturity_date": "2027-11-05",
        "credit_rating": "AA-",
        "rating_agency": "Beacon Ratings",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 195.0,
        "explainer": "Logistics hub and multi-modal park infrastructure debenture offering attractive yield pickup.",
        "risk_profile": "AA- rating. Cyclical logistics sector exposure, mitigated by strong warehousing demand."
    },
    {
        "name": "Deccan Ports & Logistics 8.35% 2030",
        "issuer_name": "Deccan Ports Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0835,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2020-12-10",
        "maturity_date": "2030-12-10",
        "credit_rating": "AAA",
        "rating_agency": "IndiCredit",
        "is_secured": True,
        "min_lot_size": 5,
        "benchmark_spread_bps": 90.0,
        "explainer": "Port handling infrastructure bonds backed by long-term container concessions.",
        "risk_profile": "Prime AAA security with high operational cash reserves and low leverage."
    },
    {
        "name": "Sterling Commercial Vehicles 8.80% 2029",
        "issuer_name": "Sterling Auto Finance Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0880,
        "coupon_frequency": "MONTHLY",
        "issue_date": "2023-01-15",
        "maturity_date": "2029-01-15",
        "credit_rating": "AA",
        "rating_agency": "TrustRatings",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 160.0,
        "explainer": "Monthly interest payout bond for regular cash flow, backed by heavy commercial vehicle loans.",
        "risk_profile": "Monthly coupon credit reduces reinvestment duration. Medium credit risk."
    },
    {
        "name": "Zenith Transmission Corp 7.95% 2032",
        "issuer_name": "Zenith Transmission Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0795,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2022-04-12",
        "maturity_date": "2032-04-12",
        "credit_rating": "AAA",
        "rating_agency": "Beacon Ratings",
        "is_secured": True,
        "min_lot_size": 5,
        "benchmark_spread_bps": 68.0,
        "explainer": "Power transmission utility bond with regulated return on equity structure.",
        "risk_profile": "Defensive utility sector. Minimal default probability."
    },
    {
        "name": "Prism Health Infrastructure 9.60% 2027",
        "issuer_name": "Prism Health Infrastructure Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0960,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2023-08-10",
        "maturity_date": "2027-08-10",
        "credit_rating": "A",
        "rating_agency": "Beacon Ratings",
        "is_secured": False,
        "min_lot_size": 20,
        "benchmark_spread_bps": 265.0,
        "explainer": "High-yield healthcare and diagnostic center expansion debentures. Unsecured ranking.",
        "risk_profile": "Rated A (investment grade but lower bucket). Higher credit spread risk, higher default vulnerability."
    },
    {
        "name": "Kaveri Agro Tech 9.40% 2028",
        "issuer_name": "Kaveri Agri Infrastructure Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0940,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2023-05-20",
        "maturity_date": "2028-05-20",
        "credit_rating": "A+",
        "rating_agency": "TrustRatings",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": 230.0,
        "explainer": "Cold-chain and grain silo modernization debentures offering high semi-annual yield.",
        "risk_profile": "A+ rating. Agri commodity cycle sensitivity, supported by central storage subsidies."
    },
    {
        "name": "Vanguard Microfinance 10.20% 2027",
        "issuer_name": "Vanguard Financial Services Ltd",
        "instrument_type": "CORPORATE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.1020,
        "coupon_frequency": "MONTHLY",
        "issue_date": "2024-01-10",
        "maturity_date": "2027-01-10",
        "credit_rating": "A",
        "rating_agency": "IndiCredit",
        "is_secured": True,
        "min_lot_size": 25,
        "benchmark_spread_bps": 320.0,
        "explainer": "High regular monthly income bond backed by micro-lending portfolio.",
        "risk_profile": "Rated A. Higher yield reflects uncollateralized retail borrower base."
    },

    # Tax-Free Bonds
    {
        "name": "National Highway Infra Tax-Free 7.35% 2031",
        "issuer_name": "National Highway Infra Trust",
        "instrument_type": "TAX_FREE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0735,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2016-01-11",
        "maturity_date": "2031-01-11",
        "credit_rating": "AAA",
        "rating_agency": "Beacon Ratings",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": -20.0,
        "explainer": "100% tax-free interest under Section 10(15)(iv)(h) of Income Tax Act. Backed by national road infrastructure.",
        "risk_profile": "Pristine credit safety. Outstanding tax-adjusted yield for high tax slab investors."
    },
    {
        "name": "Indian Railway Infra Tax-Free 7.49% 2033",
        "issuer_name": "Indian Railway Finance Entity",
        "instrument_type": "TAX_FREE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0749,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2018-03-05",
        "maturity_date": "2033-03-05",
        "credit_rating": "AAA",
        "rating_agency": "IndiCredit",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": -10.0,
        "explainer": "Sovereign-linked quasi-government entity. Interest completely exempt from income tax without TDS.",
        "risk_profile": "AAA rated. Very low credit risk. Excellent demand from HNI and retail bondholders."
    },
    {
        "name": "Rural Electrification Corp Tax-Free 7.60% 2028",
        "issuer_name": "Rural Power Capital Corp",
        "instrument_type": "TAX_FREE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0760,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2013-11-20",
        "maturity_date": "2028-11-20",
        "credit_rating": "AAA",
        "rating_agency": "TrustRatings",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": -5.0,
        "explainer": "Matures in 2028. Provides completely tax-exempt annual coupon.",
        "risk_profile": "Short duration, AAA stability, tax sheltered."
    },
    {
        "name": "Urban Waterway Development Tax-Free 7.28% 2030",
        "issuer_name": "Urban Waterway Infra Ltd",
        "instrument_type": "TAX_FREE_BOND",
        "face_value": 1000.0,
        "coupon_rate": 0.0728,
        "coupon_frequency": "ANNUAL",
        "issue_date": "2015-02-18",
        "maturity_date": "2030-02-18",
        "credit_rating": "AAA",
        "rating_agency": "Beacon Ratings",
        "is_secured": True,
        "min_lot_size": 10,
        "benchmark_spread_bps": -25.0,
        "explainer": "Exempt from Indian income tax. Financing municipal water treatment and transport hubs.",
        "risk_profile": "Government sponsored entity, low default risk."
    }
]

def generate_isin(index: int, inst_type: str) -> str:
    """Generate 12-character valid format Indian ISIN."""
    prefix = "IN"
    if inst_type in ("GSEC", "TBILL"):
        cat = "0"
        num = f"0020{index:04d}"
        chk = (index * 7) % 10
        return f"{prefix}{cat}{num}{chk}"[:12]
    elif inst_type == "SDL":
        cat = "9"
        num = f"0030{index:04d}"
        chk = (index * 3) % 10
        return f"{prefix}{cat}{num}{chk}"[:12]
    elif inst_type == "SGB":
        cat = "0"
        num = f"0080{index:04d}"
        chk = (index * 9) % 10
        return f"{prefix}{cat}{num}{chk}"[:12]
    else: # Corporate or Tax-Free
        cat = "E"
        num = f"991A{index:03d}0"
        chk = (index * 5) % 10
        return f"{prefix}{cat}{num}{chk}"[:12]

def load_or_create_shared_instruments():
    """Load existing instruments_shared.json or create one."""
    found_path = None
    for p in SHARED_INSTRUMENTS_PATHS:
        if os.path.exists(p):
            found_path = p
            break

    if found_path:
        with open(found_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if len(data) >= 30:
                return data

    # Generate fresh list with ISINs
    instruments = []
    for idx, spec in enumerate(RAW_INSTRUMENTS_SPEC, start=1):
        item = dict(spec)
        item["isin"] = generate_isin(idx, spec["instrument_type"])
        instruments.append(item)

    # Save to workspace and parent if possible
    for p in SHARED_INSTRUMENTS_PATHS:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(instruments, f, indent=2)
        except Exception:
            pass

    return instruments

def seed_database(db: Session, force_reset: bool = False):
    """Seed all tables if empty or force_reset is True."""
    if force_reset:
        db.query(PriceHistory).delete()
        db.query(Holding).delete()
        db.query(SecondaryOrder).delete()
        db.query(WalletTransaction).delete()
        db.query(PrimaryApplication).delete()
        db.query(PrimaryIssue).delete()
        db.query(LinkToken).delete()
        db.query(ApiConsent).delete()
        db.query(WebhookLog).delete()
        db.query(ApiCallLog).delete()
        db.query(Instrument).delete()
        db.query(ApiClient).delete()
        db.query(User).delete()
        db.query(SystemState).delete()
        db.commit()

    # Check if system state exists
    sys_state = db.query(SystemState).first()
    if not sys_state:
        sys_state = SystemState(
            benchmark_yield=0.0708, # 7.08%
            repo_rate=0.0650, # 6.50%
            is_market_open=True,
            simulate_outage=False,
            slow_mode=False
        )
        db.add(sys_state)
        db.commit()

    # Check if instruments seeded
    inst_count = db.query(Instrument).count()
    if inst_count < 30:
        raw_items = load_or_create_shared_instruments()
        today = date.today()
        settlement_date = today + timedelta(days=1)

        instruments_objs = []
        for item in raw_items:
            existing = db.query(Instrument).filter(Instrument.isin == item["isin"]).first()
            if existing:
                continue

            iss_d = date.fromisoformat(item["issue_date"])
            mat_d = date.fromisoformat(item["maturity_date"])
            
            # Initial yield = benchmark_yield + spread
            base_ytm = sys_state.benchmark_yield + (item["benchmark_spread_bps"] / 10000.0)
            base_ytm = max(0.01, round(base_ytm, 6))

            metrics = calculate_bond_metrics(
                face_value=item["face_value"],
                coupon_rate=item["coupon_rate"],
                frequency=item["coupon_frequency"],
                issue_date=iss_d,
                maturity_date=mat_d,
                settlement_date=settlement_date,
                ytm=base_ytm
            )

            inst = Instrument(
                isin=item["isin"],
                name=item["name"],
                issuer_name=item["issuer_name"],
                instrument_type=item["instrument_type"],
                face_value=item["face_value"],
                coupon_rate=item["coupon_rate"],
                coupon_frequency=item["coupon_frequency"],
                issue_date=iss_d,
                maturity_date=mat_d,
                credit_rating=item["credit_rating"],
                rating_agency=item["rating_agency"],
                is_secured=item.get("is_secured", True),
                is_callable=item.get("is_callable", False),
                is_puttable=item.get("is_puttable", False),
                min_lot_size=item.get("min_lot_size", 1),
                benchmark_spread_bps=item["benchmark_spread_bps"],
                current_ytm=metrics["ytm"],
                clean_price=metrics["clean_price"],
                dirty_price=metrics["dirty_price"],
                accrued_interest=metrics["accrued_interest"],
                modified_duration=metrics["modified_duration"],
                current_yield=metrics["current_yield"],
                next_coupon_date=date.fromisoformat(metrics["next_coupon_date"]) if metrics["next_coupon_date"] else mat_d,
                explainer=item.get("explainer", ""),
                risk_profile=item.get("risk_profile", ""),
                is_active=True
            )
            db.add(inst)
            instruments_objs.append(inst)

        db.commit()

        # Generate 2 years of daily yield/price history for instruments
        all_insts = db.query(Instrument).all()
        histories = []
        start_date = today - timedelta(days=730)
        
        for inst in all_insts:
            # Check if history exists
            h_count = db.query(PriceHistory).filter(PriceHistory.instrument_id == inst.id).count()
            if h_count >= 10:
                continue

            curr_y = inst.current_ytm
            # Generate sample points (e.g. every 7 days over 2 years for fast lightweight charting)
            sim_date = start_date
            while sim_date <= today:
                # Add tiny random walk
                noise = random.uniform(-0.0003, 0.0003)
                sim_y = max(0.04, min(0.12, curr_y + (random.uniform(-0.008, 0.008))))
                # clean price approx
                sim_clean = round(inst.clean_price * (1.0 + (curr_y - sim_y) * inst.modified_duration), 2)
                histories.append(PriceHistory(
                    instrument_id=inst.id,
                    date=sim_date,
                    clean_price=max(10.0, sim_clean),
                    ytm=round(sim_y, 4)
                ))
                sim_date += timedelta(days=7)

        db.bulk_save_objects(histories)
        db.commit()

    # Seed Demo Users
    user_aarav = db.query(User).filter(User.email == "aarav.mehta@example.com").first()
    if not user_aarav:
        user_aarav = User(
            email="aarav.mehta@example.com",
            full_name="Aarav Mehta",
            mobile="+91 98765 43210",
            customer_id="BB-77031",
            pan="ABCDE1234F",
            pan_masked="ABCDE****F",
            demat_account="1208160012343308",
            demat_account_masked="12081600****3308",
            bank_name="HDFC Bank",
            bank_account_masked="XXXXXX5021",
            bank_ifsc="HDFC0001234",
            wallet_balance=350000.0
        )
        db.add(user_aarav)
        db.commit()

    user_priya = db.query(User).filter(User.email == "priya.nair@example.com").first()
    if not user_priya:
        user_priya = User(
            email="priya.nair@example.com",
            full_name="Priya Nair",
            mobile="+91 98111 22334",
            customer_id="BB-88142",
            pan="XYZPK9876L",
            pan_masked="XYZPK****L",
            demat_account="1208160098767741",
            demat_account_masked="12081600****7741",
            bank_name="ICICI Bank",
            bank_account_masked="XXXXXX8812",
            bank_ifsc="ICIC0000011",
            wallet_balance=180000.0
        )
        db.add(user_priya)
        db.commit()

    # Seed Holdings for Aarav Mehta:
    # 8 holdings: 2 G-Secs, 1 SGB, 3 corporate bonds (AAA/AA), 1 tax-free bond, 1 T-bill.
    # Must have coupon due within next 30 days!
    aarav_holdings_count = db.query(Holding).filter(Holding.user_id == user_aarav.id).count()
    if aarav_holdings_count == 0:
        gsecs = db.query(Instrument).filter(Instrument.instrument_type == "GSEC").limit(2).all()
        sgbs = db.query(Instrument).filter(Instrument.instrument_type == "SGB").limit(1).all()
        corps = db.query(Instrument).filter(
            Instrument.instrument_type == "CORPORATE_BOND",
            Instrument.credit_rating.in_(["AAA", "AA+", "AA"])
        ).limit(3).all()
        tax_free = db.query(Instrument).filter(Instrument.instrument_type == "TAX_FREE_BOND").limit(1).all()
        tbills = db.query(Instrument).filter(Instrument.instrument_type == "TBILL").limit(1).all()

        aarav_instruments = gsecs + sgbs + corps + tax_free + tbills
        
        # Ensure at least one has coupon due within next 30 days
        today = date.today()
        if aarav_instruments:
            # set next coupon of first corporate bond to today + 14 days
            for inst in corps:
                inst.next_coupon_date = today + timedelta(days=14)
                db.add(inst)
            db.commit()

        units_map = {
            "GSEC": 50,
            "SGB": 25,
            "CORPORATE_BOND": 20,
            "TAX_FREE_BOND": 30,
            "TBILL": 200
        }

        for i, inst in enumerate(aarav_instruments):
            u = units_map.get(inst.instrument_type, 10)
            holding = Holding(
                holding_id=f"hld_a{i+1:03d}",
                user_id=user_aarav.id,
                instrument_id=inst.id,
                units=u,
                avg_purchase_price=round(inst.clean_price * random.uniform(0.98, 1.01), 2),
                avg_purchase_yield=round(inst.current_ytm * random.uniform(0.99, 1.02), 4)
            )
            db.add(holding)

            # Add settled buy order in transaction history
            order = SecondaryOrder(
                order_id=f"ord_a{i+1:04d}",
                user_id=user_aarav.id,
                instrument_id=inst.id,
                side="BUY",
                order_type="MARKET",
                units=u,
                execution_price=holding.avg_purchase_price,
                accrued_interest_per_unit=inst.accrued_interest,
                stamp_duty=round(holding.avg_purchase_price * u * 0.00015, 2),
                exchange_charges=25.0,
                total_consideration=round((holding.avg_purchase_price + inst.accrued_interest) * u + 25.0, 2),
                settlement_date=today - timedelta(days=random.randint(30, 150)),
                status="SETTLED"
            )
            db.add(order)

        # Seed coupon credits in wallet transactions for Aarav
        for m in range(1, 6):
            db.add(WalletTransaction(
                user_id=user_aarav.id,
                tx_type="COUPON_CREDIT",
                amount=round(random.uniform(4000.0, 12000.0), 2),
                balance_after=350000.0,
                description="Semi-annual interest coupon credited to linked bank account",
                reference_id="INE000000000",
                created_at=datetime.datetime.now(datetime.timezone.utc) - timedelta(days=30 * m)
            ))

        db.commit()

    # Seed Holdings for Priya Nair:
    # 6 holdings, corporate-heavy, one holding rated A (Prism Health Infrastructure) to trigger risk warning!
    priya_holdings_count = db.query(Holding).filter(Holding.user_id == user_priya.id).count()
    if priya_holdings_count == 0:
        rated_a = db.query(Instrument).filter(
            Instrument.instrument_type == "CORPORATE_BOND",
            Instrument.credit_rating == "A"
        ).first()

        corps_other = db.query(Instrument).filter(
            Instrument.instrument_type == "CORPORATE_BOND",
            Instrument.credit_rating != "A"
        ).limit(3).all()

        gsec_one = db.query(Instrument).filter(Instrument.instrument_type == "GSEC").limit(1).all()
        sdl_one = db.query(Instrument).filter(Instrument.instrument_type == "SDL").limit(1).all()

        priya_instruments = ([rated_a] if rated_a else []) + corps_other + gsec_one + sdl_one
        
        for i, inst in enumerate(priya_instruments):
            u = 25 if inst.instrument_type == "CORPORATE_BOND" else 15
            holding = Holding(
                holding_id=f"hld_p{i+1:03d}",
                user_id=user_priya.id,
                instrument_id=inst.id,
                units=u,
                avg_purchase_price=round(inst.clean_price * random.uniform(0.97, 1.01), 2),
                avg_purchase_yield=round(inst.current_ytm * random.uniform(0.98, 1.03), 4)
            )
            db.add(holding)

            order = SecondaryOrder(
                order_id=f"ord_p{i+1:04d}",
                user_id=user_priya.id,
                instrument_id=inst.id,
                side="BUY",
                order_type="MARKET",
                units=u,
                execution_price=holding.avg_purchase_price,
                accrued_interest_per_unit=inst.accrued_interest,
                stamp_duty=round(holding.avg_purchase_price * u * 0.00015, 2),
                exchange_charges=25.0,
                total_consideration=round((holding.avg_purchase_price + inst.accrued_interest) * u + 25.0, 2),
                settlement_date=date.today() - timedelta(days=random.randint(20, 120)),
                status="SETTLED"
            )
            db.add(order)

        db.commit()

    # Seed Open Primary Issues
    if db.query(PrimaryIssue).count() == 0:
        today = date.today()
        p1 = PrimaryIssue(
            issue_code="IPO-SUNRISE-2026",
            name="Sunrise Housing Finance Public Bond Series I",
            issuer_name="Sunrise Housing Finance Ltd",
            instrument_type="CORPORATE_BOND",
            face_value=1000.0,
            coupon_rate=0.0895, # 8.95%
            coupon_frequency="ANNUAL",
            tenor_months=60,
            rating="AA+",
            rating_agency="IndiCredit",
            min_investment=10000.0,
            open_date=today - timedelta(days=3),
            close_date=today + timedelta(days=12),
            allotment_date=today + timedelta(days=16),
            status="OPEN",
            description="Public issuance of secured redeemable non-convertible debentures to expand urban affordable housing portfolio."
        )
        p2 = PrimaryIssue(
            issue_code="SGB-2026-27-S2",
            name="Sovereign Gold Bond 2026-27 Series II",
            issuer_name="Government of India",
            instrument_type="SGB",
            face_value=6350.0,
            coupon_rate=0.0250,
            coupon_frequency="SEMI_ANNUAL",
            tenor_months=96,
            rating="SOVEREIGN",
            rating_agency="Reserve Bank of India",
            min_investment=6350.0,
            open_date=today - timedelta(days=1),
            close_date=today + timedelta(days=5),
            allotment_date=today + timedelta(days=9),
            status="OPEN",
            description="Issued by Reserve Bank of India on behalf of Government of India. ₹50/gram discount for online applications."
        )
        db.add(p1)
        db.add(p2)
        db.commit()

    # Seed Registered API Client App
    # Credentials: app_id = "portfolio-aggregator"
    # Secret in dev defaults to "bb-demo-secret"; set DEMO_API_CLIENT_SECRET in env to override.
    demo_secret = getattr(settings, "DEMO_API_CLIENT_SECRET", None) or "bb-demo-secret"
    client = db.query(ApiClient).filter(ApiClient.client_id == "portfolio-aggregator").first()
    if not client:
        client = ApiClient(
            client_id="portfolio-aggregator",
            client_secret_hash=_hash_secret(demo_secret),
            client_name="Portfolio Aggregator",
            allowed_redirect_uris="http://localhost:3000/link-complete,https://oauth.pstmn.io/v1/callback,http://127.0.0.1:3000/link-complete",
            allowed_webhook_urls="http://localhost:8000/webhooks/bondbazaar,http://127.0.0.1:8000/webhooks/bondbazaar,https://webhook.site/demo",
            is_active=True
        )
        db.add(client)
        db.commit()
