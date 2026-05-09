import keyring
import requests
import uuid

SERVICE_NAME = "freeccr-client"
ACTIVATION_KEY = "activation_key"
COMMERCIAL_FLAG_KEY = "activation_type"  # New key for storing license type
INSTANCE_ID_KEY = "instance_id"  # New key for storing instance id
EXPIRED_KEY = "expired"
INSTANCE_NAME_KEY = "instance_name"

# Configuration for abstraction layer
# Change this to your Fly.io app URL when deployed
# host = "freeccr-activation.fly.dev"  # Your Fly.io deployment
host = "localhost:8080"  # For local development

# Alternatively, you can use environment variables for easier configuration
import os
host = os.getenv("ACTIVATION_HOST", "localhost:8080")

def validate_software() -> tuple[bool, str | None]:
    """
    Validates the software activation by checking the activation key.
    Returns True if the software is activated, False otherwise.
    """
    activation_key = keyring.get_password(SERVICE_NAME, ACTIVATION_KEY)
    if not activation_key:
        return False, ""
    instance_id = keyring.get_password(SERVICE_NAME, INSTANCE_ID_KEY)
    if not instance_id:
        return False, ""
    license_type = keyring.get_password(SERVICE_NAME, COMMERCIAL_FLAG_KEY)
    # Verify the activation key online
    successful=verify_activation_key_online(activation_key,instance_id)
    if not successful:
        keyring.set_password(SERVICE_NAME, EXPIRED_KEY, "true")
    return successful, license_type

def verify_activation_key_online(activation_key: str, instance_id: str, timeout: int = 3) -> bool:
    """
    Verifies the activation key with the abstraction layer API.
    Returns True if valid, False otherwise.
    If the request fails due to timeout or offline, returns True to allow offline use.
    :param activation_key: The activation key to verify.
    :param timeout: Timeout for the HTTP request in seconds (default 3).
    """
    # Use abstraction layer endpoint
    api_url = f"https://{host}/v1/licenses/validate"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    data = {
        "license_key": activation_key,
        "instance_id": instance_id
    }
    expired = keyring.get_password(SERVICE_NAME, EXPIRED_KEY)
    try:
        response = requests.post(api_url, headers=headers, json=data, timeout=timeout)
        if response.status_code == 200:
            result = response.json()
            if expired == "true":
                keyring.set_password(SERVICE_NAME, EXPIRED_KEY, "false")
            return result.get("valid", False)
        if 500 <= response.status_code < 600:
            return expired != "true"  # Server error: treat as verified (allow offline use)
        return False
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        # Timeout or offline: treat as verified (allow offline use)
        return expired != "true"
    except Exception:
        # Other errors: treat as not verified
        return False

def is_software_activated() -> bool:
    """
    Checks if the software is activated by looking up the activation key in the system keyring.
    Returns True if the activation key is present, False otherwise.
    """
    key = keyring.get_password(SERVICE_NAME, ACTIVATION_KEY)
    return key is not None

def get_activation_key() -> str | None:
    """
    Retrieves the stored activation key from the system keyring.
    Returns the activation key if present, otherwise None.
    """
    return keyring.get_password(SERVICE_NAME, ACTIVATION_KEY)

def activate_license_online(activation_key: str, instance_name: str, email: str, timeout: int = 10) -> tuple[bool, str, str | None]:
    """
    Activates the license with the abstraction layer API.
    Returns (True, license_type, instance_id) if activation is successful, (False, None, None) otherwise.
    :param activation_key: The activation key to activate.
    :param instance_name: The name of this instance (e.g., computer/user).
    :param email: The email address for validation.
    :param timeout: Timeout for the HTTP request in seconds (default 10).
    """
    # Use abstraction layer endpoint
    url = f"https://{host}/v1/licenses/activate"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    data = {
        "license_key": activation_key,
        "instance_name": instance_name,
        "email": email
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=timeout)
        if response.status_code == 200:
            result = response.json()
            # Server now handles all validation and returns license type directly
            license_type = result.get("license_type")
            instance_id = result.get("instance", {}).get("id")
            activated = result.get("activated", False)
            
            if activated and license_type and instance_id:
                return True, license_type, instance_id
    except Exception:
        pass
    return False, None, None

def activate_software(activation_key: str, email: str, timeout: int = 10) -> bool:
    """
    Activates the software by activating the license online and storing the key, license type, and instance id if successful.
    Returns True if activation succeeded, False otherwise.
    """
    instance_name = keyring.get_password(SERVICE_NAME, INSTANCE_NAME_KEY)
    if not instance_name:
        instance_name = str(uuid.uuid4())
    success, license_type, instance_id = activate_license_online(activation_key, instance_name, email, timeout=timeout)
    if success and license_type:
        keyring.set_password(SERVICE_NAME, ACTIVATION_KEY, activation_key)
        keyring.set_password(SERVICE_NAME, COMMERCIAL_FLAG_KEY, license_type)
        keyring.set_password(SERVICE_NAME, INSTANCE_ID_KEY, instance_id)
        keyring.set_password(SERVICE_NAME, INSTANCE_NAME_KEY, instance_name)
        keyring.set_password(SERVICE_NAME, EXPIRED_KEY,"false")  # Clear expired flag if activation is successful
        return True
    return False

def deactivate_license_online(activation_key: str, instance_id: str, timeout: int = 10) -> bool:
    """
    Deactivates the license with the abstraction layer API for this instance.
    Returns True if deactivation is successful, False otherwise.
    :param activation_key: The activation key to deactivate.
    :param instance_id: The instance id to deactivate.
    :param timeout: Timeout for the HTTP request in seconds (default 10).
    """
    # Use abstraction layer endpoint
    url = f"https://{host}/v1/licenses/deactivate"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    data = {
        "license_key": activation_key,
        "instance_id": instance_id
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=timeout)
        if response.status_code == 200:
            result = response.json()
            return result.get("deactivated", False)
    except Exception:
        pass
    return False

def deactivate_software() -> bool:
    """
    Deactivates the software by calling the abstraction layer API and removing activation info from keyring.
    """
    activation_key = keyring.get_password(SERVICE_NAME, ACTIVATION_KEY)
    instance_id = keyring.get_password(SERVICE_NAME, INSTANCE_ID_KEY)
    ok = False
    if activation_key and instance_id:
        ok = deactivate_license_online(activation_key, instance_id)
    if ok:
        try:
            keyring.delete_password(SERVICE_NAME, ACTIVATION_KEY)
            keyring.delete_password(SERVICE_NAME, COMMERCIAL_FLAG_KEY)
            keyring.delete_password(SERVICE_NAME, INSTANCE_ID_KEY)
            keyring.delete_password(SERVICE_NAME, EXPIRED_KEY)
        except Exception:
            # If deletion fails, we still consider deactivation successful
            print("Failed to delete activation info from keyring, but deactivation succeeded.")
            pass
    return ok

def get_license_type() -> str | None:
    """
    Retrieves the stored license type ('Personal' or 'Commercial') from the system keyring.
    Returns the license type if present, otherwise None.
    """
    return keyring.get_password(SERVICE_NAME, COMMERCIAL_FLAG_KEY)

def get_instance_id() -> str | None:
    """
    Retrieves the stored instance id from the system keyring.
    Returns the instance id if present, otherwise None.
    """
    return keyring.get_password(SERVICE_NAME, INSTANCE_ID_KEY)
