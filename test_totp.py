#!/usr/bin/env python3
"""Test TOTP generation to verify credentials."""

import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

try:
    import pyotp
except ImportError:
    print("❌ pyotp not installed. Run: pip install pyotp")
    exit(1)

# Get credentials from .env
client_code = os.getenv("ANGEL_CLIENT_ID", os.getenv("SMARTAPI_CLIENT_CODE", ""))
totp_secret = os.getenv("ANGEL_TOTP_SECRET", os.getenv("SMARTAPI_TOTP_SECRET", ""))
pin = os.getenv("ANGEL_PIN", os.getenv("SMARTAPI_PIN", ""))

print("=" * 60)
print("🔍 TOTP & Credentials Test")
print("=" * 60)

# Validate inputs
print("\n📋 Credentials from .env:")
print(f"  Client Code: {'✅ Set' if client_code else '❌ MISSING'} ({client_code[:6]}...)" if client_code else "  Client Code: ❌ MISSING")
print(f"  PIN: {'✅ Set' if pin else '❌ MISSING'} ({len(pin)} chars)" if pin else "  PIN: ❌ MISSING")
print(f"  TOTP Secret: {'✅ Set' if totp_secret else '❌ MISSING'} ({len(totp_secret)} chars)" if totp_secret else "  TOTP Secret: ❌ MISSING")

if not all([client_code, totp_secret, pin]):
    print("\n❌ Missing required credentials in .env!")
    print("\nRequired fields:")
    print("  ANGEL_CLIENT_ID or SMARTAPI_CLIENT_CODE")
    print("  ANGEL_PIN or SMARTAPI_PIN")
    print("  ANGEL_TOTP_SECRET or SMARTAPI_TOTP_SECRET")
    exit(1)

# Validate TOTP secret format
print(f"\n🔑 TOTP Secret validation:")
print(f"  Length: {len(totp_secret)} chars (should be 16-32)")
if len(totp_secret) < 16:
    print(f"  ⚠️  WARNING: Secret might be too short (expected 16+)")

# Test TOTP generation
try:
    totp = pyotp.TOTP(totp_secret)
    current_code = totp.now()
    print(f"  ✅ Valid Base32 secret")
    print(f"\n📱 Current TOTP Codes (valid for ~30 seconds):")
    print(f"  Current:  {current_code}")
    print(f"  Time:     {datetime.utcnow().strftime('%H:%M:%S UTC')}")
    
    # Show next few codes
    for i in range(1, 4):
        next_code = totp.at(totp.timecode(datetime.utcnow()) + i*30)
        print(f"  +{i*30}s:  {next_code}")
    
except Exception as e:
    print(f"  ❌ Invalid TOTP secret: {e}")
    print(f"\n  Your secret: {totp_secret}")
    print(f"\n  Possible issues:")
    print(f"    • Secret contains invalid characters")
    print(f"    • Secret is not Base32 encoded")
    print(f"    • Secret was copied incorrectly")
    exit(1)

print("\n" + "=" * 60)
print("✅ Credentials look valid!")
print("=" * 60)
print("\nNext steps:")
print("  1. Go to Streamlit: http://localhost:8501")
print("  2. Fill in credentials:")
print(f"     Client ID: {client_code}")
print(f"     PIN: {'*' * len(pin)}")
print(f"     TOTP Secret: {'*' * len(totp_secret)}")
print("  3. Copy the CURRENT TOTP code from above into the dashboard")
print("  4. Click 'Login with PIN & Connect' within 30 seconds")
print("\nIf login still fails:")
print("  • Verify PIN is correct (check Angel One app settings)")
print("  • Check system time is correct (TOTP is time-sensitive)")
print("  • Ensure you're using a fresh TOTP code (< 10 seconds old)")
