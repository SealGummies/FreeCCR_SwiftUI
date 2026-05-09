# License Management Abstraction Layer

This Go service provides an abstraction layer for license management, allowing you to easily switch between different license providers (currently supports Lemon Squeezy) without changing your client code.

## Features

- **Provider Abstraction**: Easy to switch between different license providers
- **Compatible API**: Maintains the same API structure as Lemon Squeezy
- **Server-side Validation**: Email, store ID, and variant ID validation handled on server
- **License Type Resolution**: Automatically determines license type from variant ID
- **Error Handling**: Graceful handling of network errors and timeouts
- **Offline Support**: Allows offline use when validation fails due to network issues
- **CORS Support**: Enables cross-origin requests for web clients

## Environment Variables

- `LEMON_SQUEEZY_API_KEY`: Your Lemon Squeezy API key (optional for now, as Lemon Squeezy doesn't require it for license operations)
- `PORT`: Server port (defaults to 8080)

## API Endpoints

### Health Check
```
GET /health
```

### License Validation
```
POST /v1/licenses/validate
{
    "license_key": "your-license-key",
    "instance_id": "your-instance-id"
}
```

Response:
```json
{
    "valid": true
}
```

### License Activation
```
POST /v1/licenses/activate
{
    "license_key": "your-license-key",
    "instance_name": "your-instance-name",
    "email": "customer@example.com"
}
```

Response:
```json
{
    "activated": true,
    "instance": {
        "id": "instance-id"
    },
    "meta": {
        "store_id": 189887,
        "variant_id": 848987,
        "customer_email": "customer@example.com"
    },
    "license_type": "Personal"
}
```

### License Deactivation
```
POST /v1/licenses/deactivate
{
    "license_key": "your-license-key",
    "instance_id": "your-instance-id"
}
```

Response:
```json
{
    "deactivated": true
}
```

## Deployment on Fly.io

1. Install the Fly CLI: https://fly.io/docs/hands-on/install-flyctl/

2. Deploy the application:
```bash
fly deploy
```

3. Set environment variables (if needed):
```bash
fly secrets set LEMON_SQUEEZY_API_KEY=your-api-key
```

## Updating Your Python Client

Update your Python `activation.py` to use your Fly.io service instead of directly calling Lemon Squeezy:

```python
# Change this line:
host = "api.lemonsqueezy.com"

# To your Fly.io app URL:
host = "your-app-name.fly.dev"
```

## Adding New License Providers

To add support for a new license provider:

1. Implement the `LicenseProvider` interface:
```go
type YourProvider struct {
    // Your provider-specific fields
}

func (y *YourProvider) ValidateLicense(licenseKey, instanceID string) (*ValidateResponse, error) {
    // Your implementation
}

func (y *YourProvider) ActivateLicense(licenseKey, instanceName string) (*ActivateResponse, error) {
    // Your implementation
}

func (y *YourProvider) DeactivateLicense(licenseKey, instanceID string) (*DeactivateResponse, error) {
    // Your implementation
}
```

2. Update the `main()` function to use your new provider based on configuration.

## License Types Mapping

The service automatically handles license validation and maps Lemon Squeezy variant IDs to license types:
- `848987` (Personal) → "Personal"
- `869418` (Personal 1 Year) → "Personal" 
- `848972` (Commercial) → "Commercial"

## Server-side Validation

The abstraction layer performs all validation on the server side:

1. **Email Validation**: Ensures the provided email matches the license customer email
2. **Store ID Validation**: Verifies the license belongs to the correct store (189887)
3. **Variant ID Validation**: Checks if the variant ID is valid and determines license type
4. **License Type Resolution**: Automatically returns the appropriate license type ("Personal" or "Commercial")

This simplifies client code and centralizes business logic on the server.

## Error Handling

- Network timeouts and connection errors return success for offline use
- Server errors (5xx) are treated as temporary and allow offline use
- Invalid requests return appropriate HTTP error codes
- Invalid store or variant IDs are rejected

## Development

Run locally:
```bash
go run main.go
```

Build:
```bash
go build -o activation-server
```
