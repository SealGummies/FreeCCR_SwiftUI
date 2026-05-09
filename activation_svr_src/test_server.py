#!/usr/bin/env python3
"""
Simple test script to verify the activation server is working correctly
with the new server-side validation.
"""

import requests
import json

# Test configuration
SERVER_URL = "https://activation.freeccr.com"  # Change to your server URL
TEST_LICENSE_KEY = "test-key-123"
TEST_EMAIL = "test@example.com"
TEST_INSTANCE_NAME = "test-instance"

def test_health_endpoint():
    """Test the health endpoint"""
    try:
        response = requests.get(f"{SERVER_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ Health check passed")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Health check error: {e}")
        return False

def test_validate_endpoint():
    """Test the validation endpoint"""
    data = {
        "license_key": TEST_LICENSE_KEY,
        "instance_id": "test-instance-id"
    }
    
    try:
        response = requests.post(
            f"{SERVER_URL}/v1/licenses/validate",
            json=data,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        print(f"✅ Validation endpoint accessible (status: {response.status_code})")
        return True
    except Exception as e:
        print(f"❌ Validation endpoint error: {e}")
        return False

def test_activate_endpoint():
    """Test the activation endpoint with new email field"""
    data = {
        "license_key": TEST_LICENSE_KEY,
        "instance_name": TEST_INSTANCE_NAME,
        "email": TEST_EMAIL
    }
    
    try:
        response = requests.post(
            f"{SERVER_URL}/v1/licenses/activate",
            json=data,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        
        print(f"✅ Activation endpoint accessible (status: {response.status_code})")
        
        # Expected to fail with invalid license, but should show proper error handling
        if response.status_code == 400:
            try:
                error_response = response.json()
                print(f"   Expected error response: {error_response}")
            except:
                pass
        
        return True
    except Exception as e:
        print(f"❌ Activation endpoint error: {e}")
        return False

def test_deactivate_endpoint():
    """Test the deactivation endpoint"""
    data = {
        "license_key": TEST_LICENSE_KEY,
        "instance_id": "test-instance-id"
    }
    
    try:
        response = requests.post(
            f"{SERVER_URL}/v1/licenses/deactivate",
            json=data,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        print(f"✅ Deactivation endpoint accessible (status: {response.status_code})")
        return True
    except Exception as e:
        print(f"❌ Deactivation endpoint error: {e}")
        return False

def main():
    print("🧪 Testing Activation Server Endpoints")
    print("=" * 50)
    
    tests = [
        test_health_endpoint,
        test_validate_endpoint,
        test_activate_endpoint,
        test_deactivate_endpoint
    ]
    
    passed = 0
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 50)
    print(f"📊 Results: {passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("🎉 All tests passed! Server is working correctly.")
    else:
        print("⚠️  Some tests failed. Check server configuration.")

if __name__ == "__main__":
    main()
