# core/license.py
"""
Offline Ed25519 license system.

Flow:
  Seller side:  generate_keypair() once → keep private key secret
                sign_license(device_id, private_key) → license key string
                send license key to buyer via email

  Buyer side:   aegis activate <LICENSE_KEY>
                validate_license(license_key) → True/False
                stored in ~/.aegis/license.key
                checked on every startup — never needs internet
"""
import os
import sys
import base64
import hashlib
import socket
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption,
    load_pem_private_key, load_pem_public_key,
)
from cryptography.exceptions import InvalidSignature
from rich.console import Console

console = Console()

LICENSE_FILE = Path.home() / ".aegis" / "license.key"

# ── PUBLIC KEY BAKED INTO BINARY ─────────────────────────────────────────────
# Generated once with generate_keypair() — replace with your real key before
# distributing.  The private key NEVER goes in this file.
BAKED_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAtuKib6rK2d0ujQwJjxLxXmrXZWAhRaIjt1jTqFPlQzI=
----END PUBLIC KEY-----"""


# ── Device Fingerprint ────────────────────────────────────────────────────────

def get_device_id() -> str:
    """
    Stable device fingerprint from hostname + username.
    Consistent across reboots on the same device.
    Returns a 16-char hex string.
    """
    hostname = socket.gethostname()
    username = os.environ.get("USER", os.environ.get("USERNAME", "user"))
    raw = f"{hostname}::{username}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Seller Tools (run offline on your machine) ────────────────────────────────

def generate_keypair() -> tuple[str, str]:
    """
    Generate a fresh Ed25519 key pair.
    Run this ONCE — store the private key securely (never share it).
    Paste the public key into BAKED_PUBLIC_KEY_PEM above before building.

    Returns: (private_key_pem_str, public_key_pem_str)
    """
    private_key = Ed25519PrivateKey.generate()
    priv_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    pub_pem = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return priv_pem, pub_pem


def sign_license(device_id: str, private_key_pem: str) -> str:
    """
    Sign a device_id with your private key.
    Returns a base64 license key string to send to the buyer.

    Usage (seller script):
        priv, pub = generate_keypair()  # once
        device_id = <from buyer's activation request>
        license_key = sign_license(device_id, priv)
        # email license_key to buyer
    """
    private_key = load_pem_private_key(private_key_pem.encode(), password=None)
    signature   = private_key.sign(device_id.encode())
    return base64.urlsafe_b64encode(signature).decode()


# ── Buyer-Side Validation ─────────────────────────────────────────────────────

def validate_license(license_key: str) -> bool:
    """
    Verify the license key against this device's fingerprint.
    Uses the public key baked into the binary — fully offline.
    """
    # Dev/test bypass — set DEXAI_DEV=1 to skip license check
    if os.environ.get("DEXAI_DEV") == "1":
        return True

    try:
        pub_key   = load_pem_public_key(BAKED_PUBLIC_KEY_PEM)
        device_id = get_device_id()
        signature = base64.urlsafe_b64decode(license_key.encode())
        pub_key.verify(signature, device_id.encode())
        return True
    except (InvalidSignature, Exception):
        return False


def load_license() -> str | None:
    """Read stored license key from disk."""
    if LICENSE_FILE.exists():
        return LICENSE_FILE.read_text().strip()
    return None


def save_license(license_key: str) -> None:
    """Persist license key to disk."""
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(license_key.strip())


def activate(license_key: str) -> bool:
    """
    Validate and store a license key.
    Called by: aegis activate <KEY>
    """
    if validate_license(license_key):
        save_license(license_key)
        return True
    return False


def check_license_on_startup() -> None:
    """
    Called at the start of every command.
    Exits with a clear message if license is invalid or missing.
    """
    if os.environ.get("DEXAI_DEV") == "1":
        return  # Dev mode — skip check

    license_key = load_license()
    if not license_key:
        console.print(
            "\n[bold red]✗ No license found.[/bold red]\n\n"
            "  Purchase Aegis at: [link=https://whop.com/aegis]whop.com/aegis[/link]\n\n"
            "  After purchase, activate with:\n"
            "    [bold]aegis activate YOUR_LICENSE_KEY[/bold]\n\n"
            f"  Your device ID: [bold cyan]{get_device_id()}[/bold cyan]\n"
            "  (Include this when contacting support)\n"
        )
        sys.exit(1)

    if not validate_license(license_key):
        console.print(
            "\n[bold red]✗ Invalid license.[/bold red]\n\n"
            "  This license key does not match this device.\n"
            "  Each purchase is tied to one device.\n\n"
            "  Need help? Contact: support@aegis.app\n"
            f"  Device ID: [bold cyan]{get_device_id()}[/bold cyan]\n"
        )
        sys.exit(1)
