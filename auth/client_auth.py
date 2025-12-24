"""
Client utility to generate signed JWT tokens for API requests.

Usage:
    from client_auth import ClientAuth

    auth = ClientAuth("client_id", "path/to/private_key.pem")
    token = auth.generate_token()

    # Use in request header
    headers = {"Authorization": f"Bearer {token}"}
"""

from datetime import datetime, timedelta, timezone

import jwt


class ClientAuth:
    """Helper class for client-side JWT token generation"""

    def __init__(
        self,
        client_id: str,
        private_key_path: str,
        token_expiry_seconds: int = 3600,
    ):
        """
        Initialize client authentication.

        Args:
            client_id: Unique identifier for this client
            private_key_path: Path to the private key PEM file
            token_expiry_seconds: Token validity duration in seconds (default: 1 hour)
        """
        self.client_id = client_id
        self.token_expiry_seconds = token_expiry_seconds

        # Load private key
        with open(private_key_path, "r") as f:
            self.private_key = f.read()

    def generate_token(self) -> str:
        """
        Generate a signed JWT token.

        Returns:
            JWT token string
        """
        now = datetime.now(timezone.utc)
        exp = now + timedelta(seconds=self.token_expiry_seconds)

        payload = {
            "client_id": self.client_id,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }

        token: str = jwt.encode(payload, self.private_key, algorithm="RS256")
        return token


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python client_auth.py <client_id> <private_key_path>")
        print("Example: python client_auth.py client_app_1 client_app_1_private.pem")
        sys.exit(1)

    client_id = sys.argv[1]
    private_key_path = sys.argv[2]

    auth = ClientAuth(client_id, private_key_path)
    token = auth.generate_token()

    print(f"Generated token for client '{client_id}':")
    print(token)
    print("\nUse in request header:")
    print(f"Authorization: Bearer {token}")
