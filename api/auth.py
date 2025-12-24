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
        """Load public keys and token expiry from secret.yaml"""
        try:
            if "JWT_SECRET" in os.environ:
                config_path = os.environ["JWT_SECRET"]
            else:
                config_path = "./secret.yaml"

            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Load public keys for each client
            if "clients" in config:
                for client_id, client_config in config["clients"].items():
                    if "public_key" in client_config:
                        self.public_keys[client_id] = client_config["public_key"]

            # Load token expiry time if specified
            if "token_expiry_seconds" in config:
                self.token_expiry_seconds = config["token_expiry_seconds"]

        except FileNotFoundError:
            print("Warning: secret.yaml not found. JWT authentication disabled.")
        except Exception as e:
            print(f"Warning: Error loading JWT config: {e}")


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
