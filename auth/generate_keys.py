"""
Utility script to generate RSA key pairs for JWT authentication.

Usage:
    python generate_keys.py <client_id>

This will generate a public/private key pair and save them locally.
You should:
1. Add the public key to secret.yaml under clients.<client_id>.public_key
2. Keep the private key safe on the client side
"""

import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_key_pair(client_id: str) -> None:
    """Generate RSA key pair for a client"""

    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Extract public key
    public_key = private_key.public_key()

    # Serialize private key to PEM format
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    # Serialize public key to PEM format
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    print(f"\n=== Key Pair for Client: {client_id} ===\n")

    print("PUBLIC KEY (add to secret.yaml):")
    print("-" * 50)
    print(public_pem)

    print("\nPRIVATE KEY (keep safe on client side):")
    print("-" * 50)
    print(private_pem)

    # Save to files
    with open(f"{client_id}_private.pem", "w") as f:
        f.write(private_pem)
    with open(f"{client_id}_public.pem", "w") as f:
        f.write(public_pem)

    print("\nKeys saved to:")
    print(f"  - {client_id}_private.pem (keep secure!)")
    print(f"  - {client_id}_public.pem")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_keys.py <client_id>")
        print("Example: python generate_keys.py client_app_1")
        sys.exit(1)

    client_id = sys.argv[1]
    generate_key_pair(client_id)
