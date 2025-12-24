"""
Authentication module for API requests using JWT with public/private key pairs.

Clients sign their requests with a private key, and the server verifies
the signature using the client's public key.
"""

import os

import jwt
import yaml
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel


class JWTConfig:
    """Configuration for JWT authentication"""

    def __init__(self) -> None:
        """Load public keys from config file"""
        self.public_keys: dict[str, str] = {}
        self.token_expiry_seconds: int = 3600  # 1 hour default
        self._load_config()

    def _load_config(self) -> None:
        """
        Load public keys and token expiry from secret.yaml or environment variables
        """

        if os.path.exists("secret.yaml"):
            with open("secret.yaml", "r") as f:
                config = yaml.safe_load(f)
            clients = config.get("JWT_CLIENTS", {})
            for client_id, client_info in clients.items():
                pub_key = client_info.get("PUB_KEY")
                if pub_key:
                    self.public_keys[client_id] = pub_key

            self.token_expiry_seconds = config.get("JWT_TOKEN_EXPIRY_SECONDS", 3600)
        else:
            # find all env variables satisfy JWT_CLIENT_YOUR_APP_NAME_PUB_KEY
            for key, value in os.environ.items():
                if key.startswith("JWT_CLIENT_") and key.endswith("_PUB_KEY"):
                    client_id = key[len("JWT_CLIENT_") : -len("_PUB_KEY")]
                    self.public_keys[client_id] = value

            self.token_expiry_seconds = int(
                os.environ.get("JWT_TOKEN_EXPIRY_SECONDS", 3600)
            )

        if not self.public_keys:
            print("Warning: No JWT public keys loaded. Authentication may fail.")


jwt_config = JWTConfig()


class TokenPayload(BaseModel):
    """JWT token payload structure"""

    client_id: str
    iat: int  # issued at timestamp
    exp: int  # expiration timestamp


async def verify_jwt_token(request: Request) -> str:
    """
    Verify JWT token from request header.

    The token should be in the Authorization header:
    Authorization: Bearer <jwt_token>

    Args:
        request: The FastAPI request object

    Returns:
        client_id: The verified client ID

    Raises:
        HTTPException: If token is invalid or missing
    """
    auth_header = request.headers.get("Authorization")

    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    # Extract token from "Bearer <token>"
    parts = auth_header.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Use: Bearer <token>",
        )

    token = parts[1]

    try:
        # Decode without verification first to get the client_id
        unverified = jwt.decode(token, options={"verify_signature": False})
        client_id: str = unverified.get("client_id")

        if not client_id or client_id not in jwt_config.public_keys:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid client ID in token",
            )

        # Verify signature using the client's public key
        public_key = jwt_config.public_keys[client_id]
        jwt.decode(token, public_key, algorithms=["RS256"])

        return client_id

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signature",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
        )


def get_verified_client_id(client_id: str = Depends(verify_jwt_token)) -> str:
    """
    Dependency to get verified client ID.
    Can be used in route handlers.
    """
    return client_id
