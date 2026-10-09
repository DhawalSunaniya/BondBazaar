import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from app.database import Base

def ist_now():
    # Return timezone aware or current local UTC+5:30
    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.now(tz)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(120), unique=True, index=True, nullable=False)
    full_name = Column(String(120), nullable=False)
    mobile = Column(String(20), nullable=False)
    customer_id = Column(String(30), unique=True, index=True, nullable=False) # e.g. BB-77031
    pan = Column(String(10), nullable=False)
    pan_masked = Column(String(15), nullable=False)
    demat_account = Column(String(30), nullable=False)
    demat_account_masked = Column(String(30), nullable=False)
    bank_name = Column(String(100), default="HDFC Bank")
    bank_account_masked = Column(String(30), default="XXXXXX5021")
    bank_ifsc = Column(String(20), default="HDFC0001234")
    wallet_balance = Column(Float, default=100000.0) # In Rupees
    created_at = Column(DateTime(timezone=True), default=ist_now)

    holdings = relationship("Holding", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("SecondaryOrder", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("WalletTransaction", back_populates="user", cascade="all, delete-orphan")
    consents = relationship("ApiConsent", back_populates="user", cascade="all, delete-orphan")
    applications = relationship("PrimaryApplication", back_populates="user", cascade="all, delete-orphan")

class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, index=True)
    isin = Column(String(12), unique=True, index=True, nullable=False)
    name = Column(String(150), nullable=False)
    issuer_name = Column(String(120), nullable=False)
    instrument_type = Column(String(30), nullable=False) # GSEC, TBILL, SDL, SGB, CORPORATE_BOND, TAX_FREE_BOND
    face_value = Column(Float, nullable=False)
    coupon_rate = Column(Float, default=0.0) # e.g. 0.0810
    coupon_frequency = Column(String(20), default="SEMI_ANNUAL") # ANNUAL, SEMI_ANNUAL, MONTHLY, CUMULATIVE
    issue_date = Column(Date, nullable=False)
    maturity_date = Column(Date, nullable=False)
    credit_rating = Column(String(20), default="AAA")
    rating_agency = Column(String(50), default="Beacon Ratings")
    is_secured = Column(Boolean, default=True)
    is_callable = Column(Boolean, default=False)
    is_puttable = Column(Boolean, default=False)
    min_lot_size = Column(Integer, default=1)
    
    # Pricing fields
    benchmark_spread_bps = Column(Float, default=50.0) # Spread over 10Y benchmark in bps
    current_ytm = Column(Float, default=0.0750)
    clean_price = Column(Float, default=1000.0)
    dirty_price = Column(Float, default=1000.0)
    accrued_interest = Column(Float, default=0.0)
    modified_duration = Column(Float, default=4.5)
    current_yield = Column(Float, default=0.0750)
    next_coupon_date = Column(Date, nullable=True)

    explainer = Column(Text, default="")
    risk_profile = Column(Text, default="")
    is_active = Column(Boolean, default=True)

    price_history = relationship("PriceHistory", back_populates="instrument", cascade="all, delete-orphan")
    holdings = relationship("Holding", back_populates="instrument")
    orders = relationship("SecondaryOrder", back_populates="instrument")

class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), index=True, nullable=False)
    date = Column(Date, nullable=False)
    clean_price = Column(Float, nullable=False)
    ytm = Column(Float, nullable=False)

    instrument = relationship("Instrument", back_populates="price_history")

class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    holding_id = Column(String(30), unique=True, index=True, nullable=False) # e.g. hld_8f21
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), index=True, nullable=False)
    units = Column(Integer, default=1, nullable=False)
    avg_purchase_price = Column(Float, nullable=False)
    avg_purchase_yield = Column(Float, default=0.075)
    created_at = Column(DateTime(timezone=True), default=ist_now)

    user = relationship("User", back_populates="holdings")
    instrument = relationship("Instrument", back_populates="holdings")

class SecondaryOrder(Base):
    __tablename__ = "secondary_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String(30), unique=True, index=True, nullable=False) # e.g. ord_88192
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), index=True, nullable=False)
    side = Column(String(10), nullable=False) # BUY or SELL
    order_type = Column(String(10), default="MARKET") # MARKET or LIMIT
    limit_price = Column(Float, nullable=True)
    limit_yield = Column(Float, nullable=True)
    units = Column(Integer, nullable=False)
    execution_price = Column(Float, nullable=False)
    accrued_interest_per_unit = Column(Float, default=0.0)
    stamp_duty = Column(Float, default=0.0)
    exchange_charges = Column(Float, default=0.0)
    total_consideration = Column(Float, nullable=False)
    settlement_date = Column(Date, nullable=False) # T+1
    status = Column(String(20), default="OPEN") # OPEN, SETTLED, CANCELLED, REJECTED
    rejection_reason = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), default=ist_now)

    user = relationship("User", back_populates="orders")
    instrument = relationship("Instrument", back_populates="orders")

class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    tx_type = Column(String(30), nullable=False) # DEPOSIT, WITHDRAWAL, TRADE_DEBIT, TRADE_CREDIT, COUPON_CREDIT, REDEMPTION_CREDIT
    amount = Column(Float, nullable=False) # positive or negative
    balance_after = Column(Float, nullable=False)
    description = Column(String(250), nullable=False)
    reference_id = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=ist_now)

    user = relationship("User", back_populates="transactions")

class PrimaryIssue(Base):
    __tablename__ = "primary_issues"

    id = Column(Integer, primary_key=True, index=True)
    issue_code = Column(String(30), unique=True, index=True, nullable=False)
    name = Column(String(150), nullable=False)
    issuer_name = Column(String(120), nullable=False)
    instrument_type = Column(String(30), nullable=False)
    face_value = Column(Float, nullable=False)
    coupon_rate = Column(Float, nullable=False)
    coupon_frequency = Column(String(20), default="ANNUAL")
    tenor_months = Column(Integer, default=36)
    rating = Column(String(20), default="AAA")
    rating_agency = Column(String(50), default="Beacon Ratings")
    min_investment = Column(Float, default=10000.0)
    open_date = Column(Date, nullable=False)
    close_date = Column(Date, nullable=False)
    allotment_date = Column(Date, nullable=False)
    status = Column(String(20), default="OPEN") # UPCOMING, OPEN, CLOSED, ALLOTTED
    description = Column(Text, default="")

    applications = relationship("PrimaryApplication", back_populates="primary_issue")

class PrimaryApplication(Base):
    __tablename__ = "primary_applications"

    id = Column(Integer, primary_key=True, index=True)
    app_id = Column(String(30), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    primary_issue_id = Column(Integer, ForeignKey("primary_issues.id"), index=True, nullable=False)
    lots = Column(Integer, default=1)
    amount = Column(Float, nullable=False)
    status = Column(String(20), default="SUBMITTED") # SUBMITTED, ALLOTTED, REJECTED
    created_at = Column(DateTime(timezone=True), default=ist_now)

    user = relationship("User", back_populates="applications")
    primary_issue = relationship("PrimaryIssue", back_populates="applications")

class ApiClient(Base):
    __tablename__ = "api_clients"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(50), unique=True, index=True, nullable=False) # e.g. portfolio-aggregator
    client_secret_hash = Column(String(200), nullable=False)
    client_name = Column(String(100), nullable=False)
    allowed_redirect_uris = Column(Text, default="http://localhost:3000/link-complete")
    allowed_webhook_urls = Column(Text, default="http://localhost:8000/webhooks/bondbazaar")
    is_active = Column(Boolean, default=True)

class LinkToken(Base):
    __tablename__ = "link_tokens"

    id = Column(Integer, primary_key=True, index=True)
    link_token = Column(String(100), unique=True, index=True, nullable=False) # lnk_...
    client_id = Column(String(50), nullable=False)
    client_name = Column(String(100), nullable=False)
    scopes = Column(Text, default="[]") # JSON list of scopes
    redirect_uri = Column(String(300), nullable=False)
    webhook_url = Column(String(300), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    public_token = Column(String(100), nullable=True) # pub_...
    link_session = Column(String(100), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=ist_now)

class ApiConsent(Base):
    __tablename__ = "api_consents"

    id = Column(Integer, primary_key=True, index=True)
    consent_id = Column(String(50), unique=True, index=True, nullable=False) # cns_...
    client_id = Column(String(50), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    scopes = Column(Text, default="[]")
    access_token_hash = Column(String(200), unique=True, index=True, nullable=False)
    access_token_prefix = Column(String(20), nullable=False) # acc_... prefix for identification
    expires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), default="ACTIVE") # ACTIVE, EXPIRED, REVOKED
    webhook_url = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), default=ist_now)

    user = relationship("User", back_populates="consents")

class WebhookLog(Base):
    __tablename__ = "webhook_logs"

    id = Column(Integer, primary_key=True, index=True)
    event = Column(String(50), nullable=False) # HOLDINGS_UPDATED
    consent_id = Column(String(50), nullable=False)
    customer_id = Column(String(50), nullable=False)
    payload = Column(Text, nullable=False)
    webhook_url = Column(String(300), nullable=False)
    attempt_count = Column(Integer, default=1)
    status_code = Column(Integer, nullable=True)
    status = Column(String(20), default="PENDING") # SUCCESS, FAILED, PENDING
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=ist_now)

class ApiCallLog(Base):
    __tablename__ = "api_call_logs"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(50), nullable=True)
    consent_id = Column(String(50), nullable=True)
    endpoint = Column(String(200), nullable=False)
    method = Column(String(10), nullable=False)
    status_code = Column(Integer, nullable=False)
    ip_address = Column(String(50), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=ist_now)

class SystemState(Base):
    __tablename__ = "system_state"

    id = Column(Integer, primary_key=True, default=1)
    benchmark_yield = Column(Float, default=0.0708) # 7.08%
    repo_rate = Column(Float, default=0.0650) # 6.50%
    is_market_open = Column(Boolean, default=True)
    simulate_outage = Column(Boolean, default=False)
    outage_until = Column(DateTime(timezone=True), nullable=True)
    slow_mode = Column(Boolean, default=False)

class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(120), index=True, nullable=False)
    provider = Column(String(20), default="c", nullable=False)
    event = Column(String(50), default="HOLDINGS_CHANGED", nullable=False)
    occurred_at = Column(String(50), nullable=False)
    status = Column(String(20), default="PENDING")  # PENDING, DELIVERED, FAILED
    attempts = Column(Integer, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=ist_now)
