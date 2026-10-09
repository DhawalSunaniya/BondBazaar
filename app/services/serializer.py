import datetime
from typing import Any, Dict, List, Optional
from app.models.entities import (
    User, Holding, Instrument, SecondaryOrder, WalletTransaction
)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_current_ist_iso() -> str:
    return datetime.datetime.now(IST).replace(microsecond=0).isoformat()

def rupees_to_paise(rupees: float) -> int:
    """Converts Rupees (float) to integer Paise (never floats)."""
    if rupees is None:
        return 0
    return int(round(rupees * 100))

def rate_to_bps(rate: float) -> int:
    """Converts decimal rate (e.g. 0.0810) to basis points (e.g. 810)."""
    if rate is None:
        return 0
    return int(round(rate * 10000))

def format_date_str(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, (datetime.date, datetime.datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)

def serialize_error(code: str, detail: str) -> Dict[str, Any]:
    return {
        "errors": [
            {
                "code": code,
                "detail": detail
            }
        ]
    }

def serialize_customer(user: User) -> Dict[str, Any]:
    return {
        "data": {
            "type": "customer",
            "id": user.customer_id,
            "attributes": {
                "customerId": user.customer_id,
                "fullName": user.full_name,
                "email": user.email,
                "mobile": user.mobile,
                "maskedPan": user.pan_masked,
                "demat": {
                    "depository": "CDSL",
                    "dpName": "BondBazaar Depository Services",
                    "accountMasked": user.demat_account_masked
                },
                "bank": {
                    "bankName": user.bank_name,
                    "accountMasked": user.bank_account_masked,
                    "ifsc": user.bank_ifsc
                },
                "registeredAt": user.created_at.isoformat() if user.created_at else get_current_ist_iso()
            }
        },
        "meta": {
            "generatedAt": get_current_ist_iso()
        }
    }

def serialize_holding(holding: Holding) -> Dict[str, Any]:
    inst = holding.instrument
    units = holding.units
    face_val_paise = rupees_to_paise(inst.face_value)
    avg_price_paise = rupees_to_paise(holding.avg_purchase_price)
    curr_price_paise = rupees_to_paise(inst.clean_price)
    invested_paise = rupees_to_paise(holding.avg_purchase_price * units)
    curr_val_paise = rupees_to_paise(inst.clean_price * units)
    accrued_paise = rupees_to_paise(inst.accrued_interest * units)

    return {
        "type": "holding",
        "id": holding.holding_id,
        "attributes": {
            "isin": inst.isin,
            "instrumentName": inst.name,
            "instrumentType": inst.instrument_type,
            "units": units,
            "faceValuePaise": face_val_paise,
            "avgPurchasePricePaise": avg_price_paise,
            "currentPricePaise": curr_price_paise,
            "investedPaise": invested_paise,
            "currentValuePaise": curr_val_paise,
            "accruedInterestPaise": accrued_paise,
            "couponRateBps": rate_to_bps(inst.coupon_rate),
            "ytmBps": rate_to_bps(inst.current_ytm),
            "couponFrequency": inst.coupon_frequency,
            "nextCouponDate": format_date_str(inst.next_coupon_date),
            "maturityDate": format_date_str(inst.maturity_date),
            "rating": {
                "agency": inst.rating_agency,
                "grade": inst.credit_rating
            }
        },
        "relationships": {
            "demat": {
                "dpName": "BondBazaar Depository Services",
                "accountMasked": holding.user.demat_account_masked if holding.user else "XXXX3308"
            }
        }
    }

def serialize_holdings_list(
    holdings: List[Holding],
    limit: int = 20,
    cursor: Optional[str] = None,
    next_cursor: Optional[str] = None
) -> Dict[str, Any]:
    serialized = [serialize_holding(h) for h in holdings]
    total_invested_paise = sum(rupees_to_paise(h.avg_purchase_price * h.units) for h in holdings)
    total_current_paise = sum(rupees_to_paise(h.instrument.clean_price * h.units) for h in holdings)
    total_accrued_paise = sum(rupees_to_paise(h.instrument.accrued_interest * h.units) for h in holdings)

    links: Dict[str, Optional[str]] = {
        "self": f"/open/v1/holdings?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
    }
    if next_cursor:
        links["next"] = f"/open/v1/holdings?limit={limit}&cursor={next_cursor}"
    else:
        links["next"] = None

    return {
        "data": serialized,
        "links": links,
        "meta": {
            "generatedAt": get_current_ist_iso(),
            "count": len(serialized),
            "totalInvestedPaise": total_invested_paise,
            "totalCurrentValuePaise": total_current_paise,
            "totalAccruedInterestPaise": total_accrued_paise,
            "totalInvested": round(sum(h.avg_purchase_price * h.units for h in holdings), 2),
            "totalCurrentValue": round(sum(h.instrument.clean_price * h.units for h in holdings), 2),
        }
    }

def serialize_instrument(inst: Instrument) -> Dict[str, Any]:
    return {
        "type": "instrument",
        "id": inst.isin,
        "attributes": {
            "isin": inst.isin,
            "name": inst.name,
            "issuerName": inst.issuer_name,
            "instrumentType": inst.instrument_type,
            "faceValuePaise": rupees_to_paise(inst.face_value),
            "couponRateBps": rate_to_bps(inst.coupon_rate),
            "couponFrequency": inst.coupon_frequency,
            "currentYtmBps": rate_to_bps(inst.current_ytm),
            "cleanPricePaise": rupees_to_paise(inst.clean_price),
            "dirtyPricePaise": rupees_to_paise(inst.dirty_price),
            "accruedInterestPaise": rupees_to_paise(inst.accrued_interest),
            "currentYieldBps": rate_to_bps(inst.current_yield),
            "modifiedDuration": round(inst.modified_duration, 4),
            "issueDate": format_date_str(inst.issue_date),
            "maturityDate": format_date_str(inst.maturity_date),
            "nextCouponDate": format_date_str(inst.next_coupon_date),
            "rating": {
                "agency": inst.rating_agency,
                "grade": inst.credit_rating
            },
            "isSecured": inst.is_secured,
            "isCallable": inst.is_callable,
            "isPuttable": inst.is_puttable,
            "minLotSize": inst.min_lot_size,
            "explainer": inst.explainer,
            "riskProfile": inst.risk_profile
        }
    }

def serialize_instruments_list(instruments: List[Instrument]) -> Dict[str, Any]:
    return {
        "data": [serialize_instrument(inst) for inst in instruments],
        "meta": {
            "generatedAt": get_current_ist_iso(),
            "count": len(instruments)
        }
    }

def serialize_transaction(order: SecondaryOrder) -> Dict[str, Any]:
    inst = order.instrument
    return {
        "type": "transaction",
        "id": order.order_id,
        "attributes": {
            "side": order.side,
            "status": order.status,
            "isin": inst.isin if inst else "UNKNOWN",
            "instrumentName": inst.name if inst else "Bond",
            "units": order.units,
            "executionPricePaise": rupees_to_paise(order.execution_price),
            "accruedInterestPaise": rupees_to_paise(order.accrued_interest_per_unit * order.units),
            "stampDutyPaise": rupees_to_paise(order.stamp_duty),
            "exchangeChargesPaise": rupees_to_paise(order.exchange_charges),
            "totalConsiderationPaise": rupees_to_paise(order.total_consideration),
            "settlementDate": format_date_str(order.settlement_date),
            "createdAt": order.created_at.isoformat() if order.created_at else get_current_ist_iso()
        }
    }

def serialize_transactions_list(orders: List[SecondaryOrder]) -> Dict[str, Any]:
    return {
        "data": [serialize_transaction(o) for o in orders],
        "meta": {
            "generatedAt": get_current_ist_iso(),
            "count": len(orders)
        }
    }

def serialize_wallet(user: User, txs: List[WalletTransaction]) -> Dict[str, Any]:
    recent_ledger = []
    for tx in txs[:10]:
        recent_ledger.append({
            "type": "wallet_entry",
            "id": f"tx_{tx.id}",
            "attributes": {
                "txType": tx.tx_type,
                "amountPaise": rupees_to_paise(tx.amount),
                "balanceAfterPaise": rupees_to_paise(tx.balance_after),
                "description": tx.description,
                "referenceId": tx.reference_id,
                "occurredAt": tx.created_at.isoformat() if tx.created_at else get_current_ist_iso()
            }
        })

    return {
        "data": {
            "type": "wallet",
            "id": f"wlt_{user.customer_id}",
            "attributes": {
                "balancePaise": rupees_to_paise(user.wallet_balance),
                "currency": "INR",
                "bankAccountMasked": user.bank_account_masked,
                "recentTransactions": recent_ledger
            }
        },
        "meta": {
            "generatedAt": get_current_ist_iso()
        }
    }
