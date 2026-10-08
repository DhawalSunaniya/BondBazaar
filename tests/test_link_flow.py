import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext
import datetime
from datetime import timedelta

from app.database import Base
from app.models.entities import User, ApiClient, LinkToken, ApiConsent
from app.services.auth_service import (
    create_link_token_record,
    exchange_public_token_for_access,
    authenticate_bearer_token,
    hash_secret
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@pytest.fixture
def auth_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    user = User(
        email="aarav.mehta@example.com",
        full_name="Aarav Mehta",
        mobile="+91 98765 43210",
        customer_id="BB-77031",
        pan="ABCDE1234F",
        pan_masked="ABCDE****F",
        demat_account="1208160012343308",
        demat_account_masked="12081600****3308"
    )
    session.add(user)

    client = ApiClient(
        client_id="portfolio-aggregator",
        client_secret_hash=pwd_context.hash("bb-demo-secret"),
        client_name="Portfolio Aggregator",
        allowed_redirect_uris="http://localhost:3000/link-complete",
        allowed_webhook_urls="http://localhost:8000/webhooks/bondbazaar",
        is_active=True
    )
    session.add(client)
    session.commit()

    yield session
    session.close()

def test_full_link_flow_lifecycle(auth_db):
    client = auth_db.query(ApiClient).first()
    user = auth_db.query(User).first()

    # Step 1: Create Link Token
    link_info = create_link_token_record(
        auth_db, client, ["holdings", "profile"],
        "http://localhost:3000/link-complete",
        "http://localhost:8000/webhooks/bondbazaar",
        "http://localhost:8000"
    )
    assert link_info["linkToken"].startswith("lnk_")
    assert "/link?token=" in link_info["linkUrl"]

    # Step 2: User authorizes and gets public token
    token_rec = auth_db.query(LinkToken).filter(LinkToken.link_token == link_info["linkToken"]).first()
    token_rec.user_id = user.id
    token_rec.public_token = "pub_test12345678"
    auth_db.commit()

    # Step 3: Backend exchanges public token
    success, data, error = exchange_public_token_for_access(auth_db, client, "pub_test12345678")
    assert success is True
    assert error is None
    assert data["accessToken"].startswith("acc_")
    assert data["consentId"].startswith("cns_")
    assert data["customerId"] == "BB-77031"

    # Step 4: Validate Bearer token
    access_token = data["accessToken"]
    auth_user, consent, err = authenticate_bearer_token(auth_db, f"Bearer {access_token}")
    assert err is None
    assert auth_user is not None
    assert auth_user.customer_id == "BB-77031"
    assert consent.status == "ACTIVE"

    # Step 5: Test Revoke Consent
    consent.status = "REVOKED"
    auth_db.commit()

    auth_user2, consent2, err2 = authenticate_bearer_token(auth_db, f"Bearer {access_token}")
    assert err2 == "CONSENT_REVOKED"
    assert auth_user2 is None

    # Step 6: Test Expire Consent
    consent.status = "ACTIVE"
    consent.expires_at = datetime.datetime.now(datetime.timezone.utc) - timedelta(days=1)
    auth_db.commit()

    auth_user3, consent3, err3 = authenticate_bearer_token(auth_db, f"Bearer {access_token}")
    assert err3 == "CONSENT_EXPIRED"
    assert auth_user3 is None
