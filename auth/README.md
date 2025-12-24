# JWT Authentication Setup Guide

## For New Clients: Getting Started

### Step 1: Generate Your Key Pair

Run this command in the project root directory:

```bash
python generate_keys.py your_app_name
```

This will create two files:
- `your_app_name_private.pem` - **Keep this secret!** (your password)
- `your_app_name_public.pem` - Share with the server admin

### Step 2: Send Your Public Key to Admin

Share the contents of `your_app_name_public.pem` with the server administrator.

The public key will look like:
```
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...
(many lines of text)
-----END PUBLIC KEY-----
```

**Send the entire key (including the BEGIN and END lines) to the admin.**

### Step 3: Admin Adds You to the Server

The admin needs to register your public key on the server. This can be done in two ways:

#### Option A: Using `secret.yaml` (Local/Standard)

Add the public key to `secret.yaml`:

```yaml
JWT_CLIENTS:
  your_app_name:
    PUB_KEY: |
      -----BEGIN PUBLIC KEY-----
      (your public key here)
      -----END PUBLIC KEY-----
```

#### Option B: Using Environment Variables (Heroku/Cloud)

For platforms like Heroku where files are not persistent, use environment variables:

1.  **Single Client Variable**:
    Set an environment variable named `JWT_CLIENT_YOUR_APP_NAME_PUB_KEY` (replace `YOUR_APP_NAME` with the actual client ID in uppercase) with the content of the public key.

    ```bash
    # Example for Heroku
    heroku config:set JWT_CLIENT_YOUR_APP_NAME_PUB_KEY="-----BEGIN PUBLIC KEY-----
    ...
    -----END PUBLIC KEY-----"
    ```

Then restart the server.

---

## For Developers: Using the Key in Your Application

### Install the Auth Module

```bash
uv pip install pyjwt cryptography
```

### Generate Tokens

```python
from auth.client_auth import ClientAuth

# Initialize with your app name and private key file
auth = ClientAuth("your_app_name", "./your_app_name_private.pem")

# Generate a token (valid for 1 hour)
token = auth.generate_token()

print(f"Your token: {token}")
```

### Use Token in API Requests

```python
import requests

# Get a fresh token
token = auth.generate_token()

# Use it in requests
headers = {"Authorization": f"Bearer {token}"}

response = requests.post(
    "https://api.example.com/api/MarkFurigana/",
    json={"text": "日本語"},
    headers=headers
)

print(response.json())
```

Or with curl:

```bash
TOKEN=$(python client_auth.py your_app_name your_app_name_private.pem)

curl -X POST "http://localhost:8000/api/MarkFurigana/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"日本語"}'
```

---

## Security Reminders

⚠️ **IMPORTANT:**
- ✅ Keep your `private.pem` file secret (like a password)
- ✅ Never commit it to version control
- ✅ Never share it with anyone
- ❌ Don't hardcode the private key in your code
- ❌ Don't store it in plain text in config files
- ❌ Don't send it over the internet

---

## Troubleshooting

### Error: "Invalid token signature"
- Make sure you're using the correct `private.pem` file
- Make sure the admin added the correct `public.pem` to the server

### Error: "Token has expired"
- Generate a fresh token - they're valid for 1 hour
- Run `auth.generate_token()` again

### Error: "Missing Authorization header"
- Make sure you include the header in your request:
  ```
  Authorization: Bearer <your_token_here>
  ```

---

## Getting Help

Contact the server administrator for:
- Adding your public key to the system
- Resetting/revoking your access
- Key rotation procedures
