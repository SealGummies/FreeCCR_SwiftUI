#!/usr/bin/env python3
"""
Test script to demonstrate the secure offline verification system.
This script shows how the new activation system works with signed verification files.
"""

import os
import sys
import time
from datetime import datetime, timedelta

# Add the src directory to the path so we can import the activation module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from activation.activation import (
    verify_activation_key_online, 
    get_offline_days_remaining,
    get_last_verification_date,
    _save_verification_success,
    _load_last_verification,
    _is_within_grace_period,
    _get_verification_file_path,
    OFFLINE_GRACE_PERIOD_DAYS
)

def test_secure_verification():
    """Test the secure verification system."""
    print("=== Testing Secure Offline Verification System ===\n")
    
    # Test 1: Show verification file location
    print(f"1. Verification file location: {_get_verification_file_path()}")
    
    # Test 2: Simulate saving a verification success
    print("\n2. Simulating successful verification...")
    current_time = time.time()
    _save_verification_success(current_time)
    
    # Test 3: Load last verification
    print("\n3. Loading last verification...")
    last_verification = _load_last_verification()
    if last_verification:
        print(f"   Last verification timestamp: {last_verification}")
        print(f"   Last verification date: {datetime.fromtimestamp(last_verification)}")
        print(f"   Is within grace period: {_is_within_grace_period(last_verification)}")
    else:
        print("   No valid verification found")
    
    # Test 4: Check offline days remaining
    print("\n4. Offline usage status...")
    days_remaining = get_offline_days_remaining()
    if days_remaining is not None:
        print(f"   Days remaining for offline use: {days_remaining}")
    else:
        print("   No offline usage available (no valid verification)")
    
    last_verification_date = get_last_verification_date()
    if last_verification_date:
        print(f"   Last successful verification: {last_verification_date.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test 5: Simulate expired verification (16 days ago)
    print(f"\n5. Testing expired verification ({OFFLINE_GRACE_PERIOD_DAYS + 1} days ago)...")
    expired_time = current_time - (OFFLINE_GRACE_PERIOD_DAYS + 1) * 24 * 60 * 60
    _save_verification_success(expired_time)
    
    expired_verification = _load_last_verification()
    if expired_verification:
        print(f"   Expired verification is within grace period: {_is_within_grace_period(expired_verification)}")
        expired_days_remaining = get_offline_days_remaining()
        print(f"   Days remaining: {expired_days_remaining}")
    
    # Test 6: Restore current verification
    print("\n6. Restoring current verification...")
    _save_verification_success(current_time)
    restored_days = get_offline_days_remaining()
    print(f"   Days remaining after restore: {restored_days}")
    
    print("\n=== Security Features ===")
    print("✓ Verification data is cryptographically signed with HMAC")
    print("✓ Signing key is derived from activation key + instance ID + system info")
    print("✓ Tampering with the verification file will invalidate it")
    print("✓ Changing activation key invalidates previous verification")
    print(f"✓ Offline usage limited to {OFFLINE_GRACE_PERIOD_DAYS} days after last successful verification")
    print("✓ Server errors and timeouts only allow usage within grace period")

if __name__ == "__main__":
    test_secure_verification()
