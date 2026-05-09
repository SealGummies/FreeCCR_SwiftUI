#!/usr/bin/env python3
"""
pytest-compatible tests for FreeCCR activation system.
Run with: pytest tests/test_pytest_activation.py -v
"""

import os
import sys
import time
import tempfile
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pytest

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

@pytest.fixture
def mocked_keyring():
    """Fixture to provide mocked keyring functionality."""
    with patch('keyring.get_password', side_effect=mock_keyring_get), \
         patch('keyring.set_password', side_effect=mock_keyring_set), \
         patch('keyring.delete_password', side_effect=mock_keyring_delete):
        
        # Import and clear cache after patching
        from activation.activation import _clear_cache
        _clear_cache()
        yield

@pytest.fixture
def activation_functions(mocked_keyring):
    """Fixture to provide activation functions with mocked keyring."""
    from activation.activation import (
        _save_verification_success,
        _load_last_verification,
        _is_within_grace_period,
        get_offline_days_remaining,
        get_last_verification_date,
        _get_verification_file_path,
        _clear_verification_file,
        OFFLINE_GRACE_PERIOD_DAYS
    )
    
    # Clear any existing verification file
    _clear_verification_file()
    
    return {
        'save_verification': _save_verification_success,
        'load_verification': _load_last_verification,
        'is_within_grace_period': _is_within_grace_period,
        'get_days_remaining': get_offline_days_remaining,
        'get_last_date': get_last_verification_date,
        'get_file_path': _get_verification_file_path,
        'clear_file': _clear_verification_file,
        'grace_period_days': OFFLINE_GRACE_PERIOD_DAYS
    }

class TestActivationSecurity:
    """Test class for activation security features."""
    
    def test_verification_save_and_load(self, activation_functions):
        """Test saving and loading verification data."""
        current_time = time.time()
        
        # Save verification
        activation_functions['save_verification'](current_time)
        
        # Load verification
        loaded_time = activation_functions['load_verification']()
        
        assert loaded_time is not None
        assert abs(loaded_time - current_time) < 1
        
        # Check file exists
        verification_file = activation_functions['get_file_path']()
        assert os.path.exists(verification_file)
    
    def test_grace_period_calculation(self, activation_functions):
        """Test grace period validation."""
        current_time = time.time()
        
        # Current time should be within grace period
        assert activation_functions['is_within_grace_period'](current_time)
        
        # Expired time should not be within grace period
        expired_time = current_time - (activation_functions['grace_period_days'] + 1) * 24 * 60 * 60
        assert not activation_functions['is_within_grace_period'](expired_time)
    
    def test_days_remaining_calculation(self, activation_functions):
        """Test days remaining calculation."""
        current_time = time.time()
        
        # Save current verification
        activation_functions['save_verification'](current_time)
        days_remaining = activation_functions['get_days_remaining']()
        assert days_remaining == activation_functions['grace_period_days'] - 1  # Should be 14
        
        # Test with 10-day-old verification
        ten_days_ago = current_time - 10 * 24 * 60 * 60
        activation_functions['save_verification'](ten_days_ago)
        days_remaining_10 = activation_functions['get_days_remaining']()
        assert days_remaining_10 == 5  # 15 - 10 = 5
    
    def test_tamper_detection(self, activation_functions):
        """Test that tampered verification files are rejected."""
        current_time = time.time()
        
        # Save valid verification
        activation_functions['save_verification'](current_time)
        verification_file = activation_functions['get_file_path']()
        
        # Read and tamper with the file
        with open(verification_file, 'r') as f:
            content = json.load(f)
        
        # Tamper with timestamp
        content['data']['timestamp'] = current_time + 86400  # Add 1 day
        
        with open(verification_file, 'w') as f:
            json.dump(content, f)
        
        # Should reject tampered verification
        tampered_verification = activation_functions['load_verification']()
        assert tampered_verification is None
    
    def test_key_change_detection(self, activation_functions):
        """Test that verification is invalidated when activation key changes."""
        current_time = time.time()
        
        # Save verification with original key
        activation_functions['save_verification'](current_time)
        original_verification = activation_functions['load_verification']()
        assert original_verification is not None
        
        # Change activation key
        original_key = MOCK_KEYRING["activation_key"]
        MOCK_KEYRING["activation_key"] = "different-key-99999"
        
        # Import and clear cache to pick up new key
        from activation.activation import _clear_cache
        _clear_cache()
        
        # Should reject verification with different key
        changed_key_verification = activation_functions['load_verification']()
        assert changed_key_verification is None
        
        # Restore original key
        MOCK_KEYRING["activation_key"] = original_key
        _clear_cache()
    
    def test_file_cleanup(self, activation_functions):
        """Test verification file cleanup."""
        current_time = time.time()
        
        # Save verification
        activation_functions['save_verification'](current_time)
        verification_file = activation_functions['get_file_path']()
        assert os.path.exists(verification_file)
        
        # Clear verification file
        activation_functions['clear_file']()
        assert not os.path.exists(verification_file)
    
    def test_verification_file_structure(self, activation_functions):
        """Test that verification file has correct structure."""
        current_time = time.time()
        
        # Save verification
        activation_functions['save_verification'](current_time)
        verification_file = activation_functions['get_file_path']()
        
        # Check file structure
        with open(verification_file, 'r') as f:
            content = json.load(f)
        
        assert 'data' in content
        assert 'signature' in content
        assert 'timestamp' in content['data']
        assert 'activation_key_hash' in content['data']
        assert 'instance_id' in content['data']
        assert content['data']['timestamp'] == current_time

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
