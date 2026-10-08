from pydantic import BaseModel, Field
from typing import List, Optional

class LinkTokenRequest(BaseModel):
    client_name: str
    scopes: List[str] = Field(default_factory=lambda: ["holdings", "profile", "transactions"])
    redirect_uri: str
    webhook_url: Optional[str] = None

class LinkTokenResponse(BaseModel):
    linkToken: str
    expiresAt: str
    linkUrl: str

class ExchangeTokenRequest(BaseModel):
    publicToken: str

class ExchangeTokenResponse(BaseModel):
    accessToken: str
    consentId: str
    consentExpiresAt: str
    customerId: str

class RevokeConsentRequest(BaseModel):
    consentId: str

class RenewConsentRequest(BaseModel):
    consentId: str
