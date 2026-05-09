# FreeCCR Activation Tests

This directory contains tests for the FreeCCR activation and licensing system.

## Test Files

### `test_activation_basic.py`
Basic functionality tests for the secure verification system:
- Verification file location and structure
- Basic offline usage functionality
- Grace period calculations
- Security feature overview

### `test_activation_security.py` 
Comprehensive security tests with mocked dependencies:
- ✅ Verification file creation and signing
- ✅ Tamper detection and rejection  
- ✅ Grace period calculations
- ✅ Key change detection
- ✅ File cleanup on deactivation
- ✅ HMAC signature verification
- ✅ Offline usage time limits

### `test_pytest_activation.py`
pytest-compatible version of the security tests:
- Uses pytest fixtures for better test isolation
- Requires `pip install pytest` to run
- More structured test organization

## Running Tests

### Run All Tests
```bash
# From the tests directory
python run_tests.py

# Or from the project root
python tests/run_tests.py
```

### Run Individual Tests
```bash
# From the tests directory
python test_activation_basic.py
python test_activation_security.py

# Or from the project root  
python tests/test_activation_basic.py
python tests/test_activation_security.py
```

### Run with pytest (Optional)
If you have pytest installed:
```bash
# Install pytest first
pip install pytest

# Run pytest tests
pytest tests/test_pytest_activation.py -v

# Or run all pytest-compatible tests
pytest tests/ -v
```

## Test Dependencies

The tests use Python's built-in modules and mock the keyring functionality to avoid requiring actual system keychain access during testing.

## Security Features Tested

- **Time-limited offline usage** (15-day grace period)
- **Cryptographic signature verification** (HMAC-SHA256)
- **Tamper detection** (file modification protection)
- **Key binding** (verification tied to activation key)
- **System binding** (includes system-specific entropy)
- **Graceful error handling** (fallback behaviors)

## Expected Output

When all tests pass, you should see:
```
🎉 All tests passed!
Results: 2/2 tests passed
```

The tests verify that the activation system properly balances security with user convenience while preventing license abuse.
