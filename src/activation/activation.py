import keyring
import requests
import uuid
import os
import json
import hmac
import hashlib
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

SERVICE_NAME = "halo-ccr-client"
ACTIVATION_KEY = "activation_key"
COMMERCIAL_FLAG_KEY = "activation_type"  # New key for storing license type
INSTANCE_ID_KEY = "instance_id"  # New key for storing instance id
EXPIRED_KEY = "expired"
INSTANCE_NAME_KEY = "instance_name"

# Configuration for abstraction layer
# Change this to your Fly.io app URL when deployed
host = "activation.haloimagery.com"  # Your Fly.io deployment
# host = "localhost:8080"  # For local development

# Offline usage configuration
OFFLINE_GRACE_PERIOD_DAYS = 15
VERIFICATION_FILE_NAME = ".halo_verification"

# Cache for keyring values to minimize macOS keychain access prompts
_keyring_cache: Dict[str, Optional[str]] = {}
_cache_loaded = False


def _load_keyring_cache() -> None:
    """Load all keyring values at once to minimize keychain access prompts."""
    global _keyring_cache, _cache_loaded
    if _cache_loaded:
        return
    
    # Load all keys in a single keychain access session
    keys_to_load = [ACTIVATION_KEY, COMMERCIAL_FLAG_KEY, INSTANCE_ID_KEY, EXPIRED_KEY, INSTANCE_NAME_KEY]
    for key in keys_to_load:
        try:
            _keyring_cache[key] = keyring.get_password(SERVICE_NAME, key)
        except Exception:
            _keyring_cache[key] = None
    
    _cache_loaded = True


def _get_cached_password(key: str) -> Optional[str]:
    """Get password from cache, loading cache if needed."""
    _load_keyring_cache()
    return _keyring_cache.get(key)


def _set_cached_password(key: str, value: str) -> None:
    """Set password in both keyring and cache."""
    keyring.set_password(SERVICE_NAME, key, value)
    _keyring_cache[key] = value


def _delete_cached_password(key: str) -> None:
    """Delete password from both keyring and cache."""
    try:
        keyring.delete_password(SERVICE_NAME, key)
    except Exception:
        pass
    _keyring_cache[key] = None


def _clear_cache() -> None:
    """Clear the cache to force reload on next access."""
    global _keyring_cache, _cache_loaded
    _keyring_cache.clear()
    _cache_loaded = False


def validate_software() -> tuple[bool, str | None]:
    """
    Validates the software activation by checking the activation key.
    Returns True if the software is activated, False otherwise.
    """
    # Activation/verification gating disabled: always allow usage.
    license_type = _get_cached_password(COMMERCIAL_FLAG_KEY) or "Unlocked"
    return True, license_type

def verify_activation_key_online(activation_key: str, instance_id: str, timeout: int = 3) -> bool:
    """
    Verifies the activation key with the abstraction layer API.
    Returns True if valid, False otherwise.
    
    Security behavior:
    - On successful online verification: saves timestamp and allows usage
    - On timeout/connection error/server error: allows usage only if last successful 
      verification was within 15 days (stored in signed local file)
    - On authentication failure (4xx): denies usage immediately
    
    :param activation_key: The activation key to verify.
    :param instance_id: The instance ID to verify.
    :param timeout: Timeout for the HTTP request in seconds (default 3).
    """
    # Activation/verification gating disabled: always allow usage.
    return True

def is_software_activated() -> bool:
    """
    Checks if the software is activated by looking up the activation key in the system keyring.
    Returns True if the activation key is present, False otherwise.
    """
    # Activation/verification gating disabled: always consider active.
    return True

def get_activation_key() -> str | None:
    """
    Retrieves the stored activation key from the system keyring.
    Returns the activation key if present, otherwise None.
    """
    return _get_cached_password(ACTIVATION_KEY)

def activate_license_online(activation_key: str, instance_name: str, email: str, timeout: int = 10) -> tuple[bool, str, str | None]:
    """
    Activates the license with the abstraction layer API.
    Returns (True, license_type, instance_id) if activation is successful, (False, None, None) otherwise.
    :param activation_key: The activation key to activate.
    :param instance_name: The name of this instance (e.g., computer/user).
    :param email: The email address for validation.
    :param timeout: Timeout for the HTTP request in seconds (default 10).
    """
    # Activation/verification gating disabled: pretend activation succeeded.
    instance_id = _get_cached_password(INSTANCE_ID_KEY) or str(uuid.uuid4())
    return True, "Unlocked", instance_id

def activate_software(activation_key: str, email: str, timeout: int = 10) -> bool:
    """
    Activates the software by activating the license online and storing the key, license type, and instance id if successful.
    Returns True if activation succeeded, False otherwise.
    """
    # Activation/verification gating disabled: store optional metadata and succeed.
    instance_name = _get_cached_password(INSTANCE_NAME_KEY) or str(uuid.uuid4())
    _set_cached_password(ACTIVATION_KEY, activation_key or "unlocked")
    _set_cached_password(COMMERCIAL_FLAG_KEY, "Unlocked")
    _set_cached_password(INSTANCE_ID_KEY, _get_cached_password(INSTANCE_ID_KEY) or str(uuid.uuid4()))
    _set_cached_password(INSTANCE_NAME_KEY, instance_name)
    _set_cached_password(EXPIRED_KEY, "false")
    return True

def deactivate_license_online(activation_key: str, instance_id: str, timeout: int = 10) -> bool:
    """
    Deactivates the license with the abstraction layer API for this instance.
    Returns True if deactivation is successful, False otherwise.
    :param activation_key: The activation key to deactivate.
    :param instance_id: The instance id to deactivate.
    :param timeout: Timeout for the HTTP request in seconds (default 10).
    """
    # Activation/verification gating disabled: deactivation endpoint is a no-op success.
    return True

def deactivate_software() -> bool:
    """
    Deactivates the software by calling the abstraction layer API and removing activation info from keyring.
    """
    # Activation/verification gating disabled: clear local values and always succeed.
    try:
        _delete_cached_password(ACTIVATION_KEY)
        _delete_cached_password(COMMERCIAL_FLAG_KEY)
        _delete_cached_password(INSTANCE_ID_KEY)
        _delete_cached_password(EXPIRED_KEY)
        _clear_verification_file()
    except Exception:
        pass
    return True

def get_license_type() -> str | None:
    """
    Retrieves the stored license type ('Personal' or 'Commercial') from the system keyring.
    Returns the license type if present, otherwise None.
    """
    return _get_cached_password(COMMERCIAL_FLAG_KEY)

def get_instance_id() -> str | None:
    """
    Retrieves the stored instance id from the system keyring.
    Returns the instance id if present, otherwise None.
    """
    return _get_cached_password(INSTANCE_ID_KEY)


def refresh_activation_cache() -> None:
    """
    Clear the activation cache to force fresh keyring access on next call.
    Useful when activation status might have changed externally.
    """
    _clear_cache()

def _get_app_data_dir() -> str:
    """Get the application data directory for storing verification files."""
    if os.name == 'nt':  # Windows
        app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
        return os.path.join(app_data, 'HaloImageryCCR')
    elif hasattr(os, 'uname') and os.uname().sysname == 'Darwin':  # macOS
        return os.path.join(os.path.expanduser('~/Library/Application Support'), 'HaloImageryCCR')
    else:  # Linux and others
        return os.path.join(os.path.expanduser('~/.local/share'), 'HaloImageryCCR')


def _get_verification_file_path() -> str:
    """Get the full path to the verification file."""
    app_dir = _get_app_data_dir()
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, VERIFICATION_FILE_NAME)


def _get_signing_key() -> str:
    """Get a signing key derived from system and activation info."""
    # Use a combination of system info and activation key to create a signing key
    activation_key = _get_cached_password(ACTIVATION_KEY) or ""
    instance_id = _get_cached_password(INSTANCE_ID_KEY) or ""
    
    # Add some system-specific entropy
    if hasattr(os, 'uname'):
        uname_info = os.uname()
        system_info = f"{uname_info.machine}-{uname_info.sysname}"
    else:
        system_info = "unknown"
    
    # Create a deterministic but hard-to-guess signing key
    key_material = f"{activation_key}-{instance_id}-{system_info}-halo-b1i2g3p4i5g6h7e8a9d-salt"
    return hashlib.sha256(key_material.encode()).hexdigest()


def _sign_data(data: str) -> str:
    """Create HMAC signature for the given data."""
    signing_key = _get_signing_key()
    return hmac.new(signing_key.encode(), data.encode(), hashlib.sha256).hexdigest()


def _verify_signature(data: str, signature: str) -> bool:
    """Verify HMAC signature for the given data."""
    expected_signature = _sign_data(data)
    return hmac.compare_digest(expected_signature, signature)


def _save_verification_success(timestamp: float) -> None:
    """Save successful verification timestamp to signed file."""
    try:
        verification_data = {
            "timestamp": timestamp,
            "activation_key_hash": hashlib.sha256((_get_cached_password(ACTIVATION_KEY) or "").encode()).hexdigest(),
            "instance_id": _get_cached_password(INSTANCE_ID_KEY) or ""
        }
        
        data_str = json.dumps(verification_data, sort_keys=True)
        signature = _sign_data(data_str)
        
        signed_data = {
            "data": verification_data,
            "signature": signature
        }
        
        verification_file = _get_verification_file_path()
        with open(verification_file, 'w') as f:
            json.dump(signed_data, f)
    except Exception:
        # If we can't save verification, fail silently
        # The system will fall back to online verification
        pass


def _load_last_verification() -> Optional[float]:
    """Load and verify the last successful verification timestamp."""
    try:
        verification_file = _get_verification_file_path()
        if not os.path.exists(verification_file):
            return None
        
        with open(verification_file, 'r') as f:
            signed_data = json.load(f)
        
        data = signed_data.get("data", {})
        signature = signed_data.get("signature", "")
        
        # Verify signature
        data_str = json.dumps(data, sort_keys=True)
        if not _verify_signature(data_str, signature):
            return None
        
        # Verify the activation key hasn't changed
        current_key_hash = hashlib.sha256((_get_cached_password(ACTIVATION_KEY) or "").encode()).hexdigest()
        if data.get("activation_key_hash") != current_key_hash:
            return None
        
        # Verify instance ID matches
        if data.get("instance_id") != _get_cached_password(INSTANCE_ID_KEY):
            return None
        
        return data.get("timestamp")
    except Exception:
        return None


def _is_within_grace_period(last_verification: float) -> bool:
    """Check if the last verification is within the grace period."""
    if last_verification is None:
        return False
    
    current_time = time.time()
    grace_period_seconds = OFFLINE_GRACE_PERIOD_DAYS * 24 * 60 * 60
    return (current_time - last_verification) <= grace_period_seconds


def _clear_verification_file() -> None:
    """Clear the verification file when software is deactivated."""
    try:
        verification_file = _get_verification_file_path()
        if os.path.exists(verification_file):
            os.remove(verification_file)
    except Exception:
        pass

def get_offline_days_remaining() -> Optional[int]:
    """
    Get the number of days remaining for offline usage.
    Returns None if no valid verification exists, or the number of days remaining.
    """
    last_verification = _load_last_verification()
    if last_verification is None:
        return None
    
    current_time = time.time()
    days_since_verification = (current_time - last_verification) / (24 * 60 * 60)
    days_remaining = OFFLINE_GRACE_PERIOD_DAYS - days_since_verification
    
    return max(0, int(days_remaining)) if days_remaining > 0 else 0


def get_last_verification_date() -> Optional[datetime]:
    """
    Get the datetime of the last successful verification.
    Returns None if no valid verification exists.
    """
    last_verification = _load_last_verification()
    if last_verification is None:
        return None
    
    return datetime.fromtimestamp(last_verification)