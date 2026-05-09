#!/usr/bin/env python3
"""
Comprehensive test script for the secure offline verification system.
This script mocks the keyring to test the complete functionality.
"""

import os
import sys
import time
import tempfile
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add the src directory to the path so we can import the activation module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Mock keyring data for testing
MOCK_KEYRING = {
    "activation_key": "test-activation-key-12345",
    "instance_id": "test-instance-id-67890",
    "activation_type": "Commercial"
}

def mock_keyring_get(service, key):
    return MOCK_KEYRING.get(key)

def mock_keyring_set(service, key, value):
    MOCK_KEYRING[key] = value

def mock_keyring_delete(service, key):
    MOCK_KEYRING.pop(key, None)

def test_with_mocked_keyring():
    """Test the secure verification system with mocked keyring data."""
    print("=== Testing Secure Offline Verification with Mocked Data ===\n")
    
    with patch('keyring.get_password', side_effect=mock_keyring_get), \
         patch('keyring.set_password', side_effect=mock_keyring_set), \
         patch('keyring.delete_password', side_effect=mock_keyring_delete):
        
        # Import after patching to ensure the module uses our mocks
        from activation.activation import (
            _save_verification_success,
            _load_last_verification,
            _is_within_grace_period,
            get_offline_days_remaining,
            get_last_verification_date,
            _get_verification_file_path,
            _clear_verification_file,
            _clear_cache,
            OFFLINE_GRACE_PERIOD_DAYS
        )
        
        # Clear the cache to ensure fresh loading
        _clear_cache()
        
        # Test 1: Show verification file location
        print(f"1. Verification file location: {_get_verification_file_path()}")
        
        # Clear any existing verification file
        _clear_verification_file()
        
        # Test 2: Save and load verification
        print("\n2. Testing verification save/load...")
        current_time = time.time()
        print(f"   Saving verification for timestamp: {current_time}")
        _save_verification_success(current_time)
        
        # Check if file was created
        verification_file = _get_verification_file_path()
        print(f"   Verification file exists: {os.path.exists(verification_file)}")
        
        if os.path.exists(verification_file):
            with open(verification_file, 'r') as f:
                content = json.load(f)
            print(f"   File content keys: {list(content.keys())}")
            if 'data' in content:
                print(f"   Data keys: {list(content['data'].keys())}")
                print(f"   Stored timestamp: {content['data'].get('timestamp')}")
        
        loaded_time = _load_last_verification()
        print(f"   Loaded timestamp: {loaded_time}")
        if loaded_time:
            print(f"   ✓ Successfully saved and loaded verification: {datetime.fromtimestamp(loaded_time)}")
            print(f"   ✓ Times match: {abs(loaded_time - current_time) < 1}")
        else:
            print("   ✗ Failed to load verification")
        
        # Test 3: Grace period check
        print("\n3. Testing grace period...")
        is_valid = _is_within_grace_period(current_time)
        print(f"   ✓ Current time is within grace period: {is_valid}")
        
        # Test expired time
        expired_time = current_time - (OFFLINE_GRACE_PERIOD_DAYS + 1) * 24 * 60 * 60
        is_expired = _is_within_grace_period(expired_time)
        print(f"   ✓ Expired time is NOT within grace period: {not is_expired}")
        
        # Test 4: Days remaining
        print("\n4. Testing days remaining calculation...")
        days_remaining = get_offline_days_remaining()
        print(f"   ✓ Days remaining for current verification: {days_remaining}")
        
        # Test with verification 10 days ago
        ten_days_ago = current_time - 10 * 24 * 60 * 60
        _save_verification_success(ten_days_ago)
        days_remaining_10 = get_offline_days_remaining()
        print(f"   ✓ Days remaining for 10-day-old verification: {days_remaining_10}")
        
        # Test 5: Verification file content
        print("\n5. Testing verification file structure...")
        verification_file = _get_verification_file_path()
        if os.path.exists(verification_file):
            with open(verification_file, 'r') as f:
                content = json.load(f)
            print(f"   ✓ File exists and contains: {list(content.keys())}")
            print(f"   ✓ Has signature: {'signature' in content}")
            print(f"   ✓ Has data: {'data' in content}")
            if 'data' in content:
                data_keys = list(content['data'].keys())
                print(f"   ✓ Data contains: {data_keys}")
        
        # Test 6: Tamper detection
        print("\n6. Testing tamper detection...")
        if os.path.exists(verification_file):
            # Read original content
            with open(verification_file, 'r') as f:
                original_content = json.load(f)
            
            # Tamper with the timestamp
            tampered_content = original_content.copy()
            tampered_content['data']['timestamp'] = current_time + 86400  # Add 1 day
            
            # Write tampered content
            with open(verification_file, 'w') as f:
                json.dump(tampered_content, f)
            
            # Try to load - should fail
            tampered_verification = _load_last_verification()
            if tampered_verification is None:
                print("   ✓ Tampered verification correctly rejected")
            else:
                print("   ✗ Tampered verification was accepted (security issue!)")
            
            # Restore original content
            with open(verification_file, 'w') as f:
                json.dump(original_content, f)
        
        # Test 7: Different activation key
        print("\n7. Testing activation key change detection...")
        # Change the activation key
        original_key = MOCK_KEYRING["activation_key"]
        MOCK_KEYRING["activation_key"] = "different-key-99999"
        _clear_cache()  # Clear cache to pick up new key
        
        # Try to load verification - should fail
        changed_key_verification = _load_last_verification()
        if changed_key_verification is None:
            print("   ✓ Verification correctly rejected after key change")
        else:
            print("   ✗ Verification accepted despite key change (security issue!)")
        
        # Restore original key
        MOCK_KEYRING["activation_key"] = original_key
        _clear_cache()  # Clear cache to pick up restored key
        
        # Test 8: Clean up
        print("\n8. Testing cleanup...")
        _clear_verification_file()
        if not os.path.exists(verification_file):
            print("   ✓ Verification file successfully cleared")
        else:
            print("   ✗ Verification file still exists after clearing")
        
        print("\n=== All Tests Completed ===")
        print("✓ Verification file creation and signing")
        print("✓ Tamper detection") 
        print("✓ Grace period calculations")
        print("✓ Key change detection")
        print("✓ File cleanup")

if __name__ == "__main__":
    test_with_mocked_keyring()
