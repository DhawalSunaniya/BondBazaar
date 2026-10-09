"""
scripts/setup_env.py — Interactive .env setup for BondBazaar.

Run once per machine / deployment:
    python scripts/setup_env.py

What it does:
  1. Checks if .env already exists. If yes, asks before overwriting.
  2. Reads .env.example as the template.
  3. Auto-fills per-site secrets (SECRET_KEY, SESSION_SECRET,
     WEBHOOK_SIGNING_SECRET, ADMIN_PASSWORD) with secrets.token_urlsafe(32).
  4. Asks for SHARED_IDENTITY_SALT and INTERNAL_API_KEY via input()
     — these must be identical on all four sibling sites.
  5. Writes the result to .env.

Never overwrites .env without asking first.
"""

import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env")
EXAMPLE_FILE = os.path.join(ROOT, ".env.example")

# Per-site secrets auto-filled with secure random values
AUTO_GEN = {
    "SECRET_KEY",
    "SESSION_SECRET",
    "WEBHOOK_SIGNING_SECRET",
    "ADMIN_PASSWORD",
}

# Shared secrets — must be identical across all four sibling sites
SHARED = {
    "SHARED_IDENTITY_SALT",
    "INTERNAL_API_KEY",
}


def _generate() -> str:
    return secrets.token_urlsafe(32)


def main() -> None:
    # ── Safety check ─────────────────────────────────────────────────────────
    if os.path.exists(ENV_FILE):
        print(f"\n⚠️  .env already exists at: {ENV_FILE}")
        answer = input("Overwrite it? (yes/no) [no]: ").strip().lower()
        if answer not in ("yes", "y"):
            print("Aborted. Your existing .env was not changed.")
            sys.exit(0)

    # ── Read template ─────────────────────────────────────────────────────────
    if not os.path.exists(EXAMPLE_FILE):
        print(f"❌  .env.example not found at: {EXAMPLE_FILE}")
        sys.exit(1)

    with open(EXAMPLE_FILE, "r", encoding="utf-8") as f:
        template = f.read()

    # ── Collect shared secrets interactively ──────────────────────────────────
    print("\n── BondBazaar .env Setup ─────────────────────────────────────────")
    print("The following two values MUST be identical on all four sibling sites")
    print("(BondBazaar, NiftyTrade, BharatInvest, TradeOne).\n")

    shared_salt = input("Enter SHARED_IDENTITY_SALT (shared across all sites): ").strip()
    if not shared_salt:
        print("❌  SHARED_IDENTITY_SALT cannot be empty.")
        sys.exit(1)

    internal_key = input("Enter INTERNAL_API_KEY    (shared across all sites): ").strip()
    if not internal_key:
        print("❌  INTERNAL_API_KEY cannot be empty.")
        sys.exit(1)

    # ── Substitution map ──────────────────────────────────────────────────────
    substitutions = {name: _generate() for name in AUTO_GEN}
    substitutions["SHARED_IDENTITY_SALT"] = shared_salt
    substitutions["INTERNAL_API_KEY"] = internal_key

    # Apply substitutions line-by-line
    out_lines = []
    for line in template.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in substitutions:
            out_lines.append(f"{key}={substitutions[key]}")
        else:
            out_lines.append(line)

    output = "\n".join(out_lines) + "\n"

    # ── Write .env ────────────────────────────────────────────────────────────
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"\n✅  .env written to: {ENV_FILE}")
    print("\nSecrets auto-generated:")
    for name in sorted(AUTO_GEN):
        print(f"  {name:<35} (random, {len(substitutions[name])} chars)")
    print("\nShared secrets (must match sibling sites):")
    print(f"  SHARED_IDENTITY_SALT              SET")
    print(f"  INTERNAL_API_KEY                  SET")
    print("\nNext steps:")
    print("  • Set the same SHARED_IDENTITY_SALT and INTERNAL_API_KEY on all four sites.")
    print("  • For Render: copy each value into Environment variables (Render ignores .env).")
    print("  • Verify .env is in .gitignore — never commit it.\n")


if __name__ == "__main__":
    main()
