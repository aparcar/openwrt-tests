#!/usr/bin/env python3
"""
Generate a JWT token for a KernelCI lab.

Usage:
    python generate-lab-token.py <lab_name> <secret_key> [--expires-days N]
    
Example:
    python generate-lab-token.py aparcar "your-secret-key" --expires-days 365
    
The secret key must match the KCI_SECRET_KEY used by the KernelCI API.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

try:
    import jwt
except ImportError:
    print("Error: PyJWT not installed. Run: pip install pyjwt", file=sys.stderr)
    sys.exit(1)


def generate_token(
    lab_name: str,
    secret_key: str,
    expires_days: int = 365,
    algorithm: str = "HS256",
) -> str:
    """Generate a JWT token for a lab."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=expires_days)
    
    payload = {
        "sub": lab_name,
        "type": "lab",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a JWT token for a KernelCI lab"
    )
    parser.add_argument("lab_name", help="Lab name (e.g., aparcar)")
    parser.add_argument("secret_key", help="KCI_SECRET_KEY from the API server")
    parser.add_argument(
        "--expires-days",
        type=int,
        default=365,
        help="Token expiration in days (default: 365)",
    )
    parser.add_argument(
        "--algorithm",
        default="HS256",
        help="JWT algorithm (default: HS256)",
    )
    
    args = parser.parse_args()
    
    if len(args.secret_key) < 32:
        print(
            "Warning: Secret key should be at least 32 characters",
            file=sys.stderr,
        )
    
    token = generate_token(
        lab_name=args.lab_name,
        secret_key=args.secret_key,
        expires_days=args.expires_days,
        algorithm=args.algorithm,
    )
    
    print(f"# Token for lab: {args.lab_name}")
    print(f"# Expires: {datetime.now(timezone.utc) + timedelta(days=args.expires_days)}")
    print(f"KCI_API_TOKEN={token}")


if __name__ == "__main__":
    main()
