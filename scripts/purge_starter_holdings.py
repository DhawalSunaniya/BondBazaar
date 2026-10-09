"""
Purge starter/seeded holdings script for BondBazaar.

Finds users who have holdings but no executed trades backing them.
Displays the identified holdings, and with `--confirm` deletes them and fires
a HOLDINGS_CHANGED outbox event for each affected user.

Real executed trades and primary allotments are never touched.

Usage:
  python scripts/purge_starter_holdings.py             # Dry run (list only)
  python scripts/purge_starter_holdings.py --confirm   # Delete unbacked holdings and fire outbox events
  python scripts/purge_starter_holdings.py --email user@example.com --confirm
"""

import argparse
import os
import sys

# Ensure repository root is in python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models.entities import User, Holding, SecondaryOrder, PrimaryApplication, Instrument
from app.services.tradeone_outbox import create_outbox_event, trigger_outbox_delivery
from app.shared_identity import normalize_email

DEMO_EMAILS = {"aarav.mehta@example.com", "priya.nair@example.com"}


def find_unbacked_holdings(db, target_email=None):
    """
    Finds all holdings that have no backing executed SecondaryOrder
    or PrimaryApplication allotment.
    """
    query = db.query(User)
    if target_email:
        query = query.filter(User.email == normalize_email(target_email))
    users = query.all()

    unbacked_by_user = {}

    for user in users:
        # Never touch demo accounts in demo mode
        if user.email in DEMO_EMAILS:
            continue

        holdings = db.query(Holding).filter(Holding.user_id == user.id).all()
        for h in holdings:
            # Check backing executed trade
            has_trade = (
                db.query(SecondaryOrder)
                .filter(
                    SecondaryOrder.user_id == user.id,
                    SecondaryOrder.instrument_id == h.instrument_id,
                    SecondaryOrder.status.in_(["SETTLED", "EXECUTED"]),
                )
                .first()
            )
            has_allotment = (
                db.query(PrimaryApplication)
                .filter(
                    PrimaryApplication.user_id == user.id,
                    PrimaryApplication.status == "ALLOTTED",
                )
                .first()
            )

            if not has_trade and not has_allotment:
                if user not in unbacked_by_user:
                    unbacked_by_user[user] = []
                unbacked_by_user[user].append(h)

    return unbacked_by_user


def main():
    parser = argparse.ArgumentParser(
        description="Purge starter holdings unbacked by real executed trades."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Execute deletion and fire HOLDINGS_CHANGED outbox events. Without this, runs dry-run.",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Optional: target a specific user email.",
    )

    args = parser.parse_args()

    db = SessionLocal()
    try:
        unbacked = find_unbacked_holdings(db, target_email=args.email)

        total_holdings = sum(len(h_list) for h_list in unbacked.values())
        total_users = len(unbacked)

        if total_holdings == 0:
            print("[INFO] No unbacked starter holdings found. All user holdings are backed by real trades.")
            return

        print("=" * 80)
        print(f"IDENTIFIED UNBACKED STARTER HOLDINGS ({total_holdings} holding(s) across {total_users} user(s)):")
        print("=" * 80)

        for user, holdings in unbacked.items():
            print(f"\nUser: {user.full_name} ({user.email}) [Customer ID: {user.customer_id}]")
            for h in holdings:
                inst = h.instrument
                inst_name = inst.name if inst else "Unknown"
                isin = inst.isin if inst else "Unknown"
                print(
                    f"  - Holding ID: {h.holding_id:<12} | ISIN: {isin:<12} | "
                    f"Units: {h.units:<4} | Avg Price: INR {h.avg_purchase_price:<10.2f} | "
                    f"Name: {inst_name} | (Backing trades: 0)"
                )

        print("\n" + "=" * 80)

        if not args.confirm:
            print("[DRY RUN ONLY] No changes were made to the database.")
            print("Run with '--confirm' to delete these unbacked holdings and fire HOLDINGS_CHANGED events.")
            print("Real executed trades and holdings backed by trades will never be touched.")
            return

        print("[CONFIRMATION RECEIVED] Purging unbacked holdings...")
        events_to_trigger = []

        for user, holdings in unbacked.items():
            for h in holdings:
                db.delete(h)
            # Insert outbox event in same transaction
            evt = create_outbox_event(db, user.email, event="HOLDINGS_CHANGED", provider="c")
            events_to_trigger.append(evt)

        db.commit()

        # Trigger background delivery for outbox events
        for evt in events_to_trigger:
            trigger_outbox_delivery(evt.event_id)

        print(
            f"[SUCCESS] Purged {total_holdings} unbacked starter holding(s) across {total_users} user(s)."
        )
        print(f"[SUCCESS] Dispatched {len(events_to_trigger)} HOLDINGS_CHANGED outbox event(s) to TradeOne.")
        print("[SUCCESS] Real executed trades were preserved and untouched.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
