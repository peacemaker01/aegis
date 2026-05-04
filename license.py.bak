# core/license.py
"""
Offline Ed25519 license system with time‑limited licenses.

License key format:
    <url-safe-base64-signature>:<unix-expiry-timestamp>

Example:
    xYz_abc...:1746000000

Flow:
  Seller side:  generate_keypair() once → keep private key secret
                sign_license(device_id, private_key, expiry) → license key string
                send license key to buyer via email

  Buyer side:   aegis activate <LICENSE_KEY>
                validate_license(license_key) → True/False (also checks expiry)
                stored in ~/.aegis/license.key
                checked on every startup — never needs internet
"""
import os
import sys
import base64
import hashlib
import socket
import time
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
BAKED_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAtuKib6rK2d0ujQwJjxLxXmrXZWAhRaIjt1jTqFPlQzI=
-----END PUBLIC KEY-----"""


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


def sign_license(device_id: str, private_key_pem: str, expiry: int = None) -> str:
    """
    Sign a device_id with your private key, optionally including an expiry.
    Returns a base64 license key string to send to the buyer.

    If expiry is provided, the license key format becomes:
        signature:expiry

    Usage (seller script):
        priv, pub = generate_keypair()  # once
        device_id = <from buyer's activation request>
        expiry = int(time.time()) + 30 * 86400  # 30 days from now
        license_key = sign_license(device_id, priv, expiry)
        # email license_key to buyer
    """
    private_key = load_pem_private_key(private_key_pem.encode(), password=None)
    
    if expiry is not None:
        message = f"{device_id}:{expiry}".encode()
        signature = private_key.sign(message)
        sig_b64 = base64.urlsafe_b64encode(signature).decode()
        return f"{sig_b64}:{expiry}"
    else:
        # Permanent license (backward compatibility)
        signature = private_key.sign(device_id.encode())
        return base64.urlsafe_b64encode(signature).decode()


# ── Buyer-Side Validation ─────────────────────────────────────────────────────

def validate_license(license_key: str) -> tuple[bool, str]:
    """
    Verify the license key against this device's fingerprint.
    Also checks expiry if present in the key format.

    Returns: (is_valid, message)
    """
    # Dev/test bypass — set AEGIS_DEV=1 to skip license check
    if os.environ.get("AEGIS_DEV") == "1":
        return True, "dev mode"

    try:
        # Check if the license key contains an expiry (format: signature:expiry)
        if ":" in license_key:
            sig_part, expiry_str = license_key.rsplit(":", 1)
            try:
                expiry = int(expiry_str)
                if time.time() > expiry:
                    return False, f"License expired on {time.ctime(expiry)}"
            except ValueError:
                return False, "Invalid expiry format"
        else:
            sig_part = license_key
            expiry = None

        # Decode signature
        padding = "=" * ((4 - len(sig_part) % 4) % 4)
        sig_bytes = base64.urlsafe_b64decode(sig_part + padding)

        # Load public key and get device ID
        pub_key = load_pem_public_key(BAKED_PUBLIC_KEY_PEM)
        device_id = get_device_id()

        # Verify signature
        if expiry is not None:
            message = f"{device_id}:{expiry}".encode()
        else:
            message = device_id.encode()

        pub_key.verify(sig_bytes, message)
        return True, "valid"
    except (InvalidSignature, Exception) as e:
        return False, str(e)


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
    valid, msg = validate_license(license_key)
    if valid:
        save_license(license_key)
        console.print(f"[green]✓ License activated. {msg}[/green]")
        return True
    console.print(f"[red]✗ Invalid license: {msg}[/red]")
    return False


def check_license_on_startup() -> None:
    """
    Called at the start of every command.
    Exits with a clear message if license is invalid or missing.
    """
    if os.environ.get("AEGIS_DEV") == "1":
        return

    license_key = load_license()
    if not license_key:
        console.print(
            "\n[bold red]✗ No license found.[/bold red]\n\n"
            "  To use Aegis Pro, hold 100,000 $AEGIS tokens and visit:\n"
            "  [link]https://yourusername.github.io/aegis-license/[/link]\n\n"
            "  After getting your license key, activate with:\n"
            "    [bold]aegis activate YOUR_LICENSE_KEY[/bold]\n\n"
            f"  Your device ID: [bold cyan]{get_device_id()}[/bold cyan]\n"
        )
        sys.exit(1)

    valid, msg = validate_license(license_key)
    if not valid:
        console.print(
            f"\n[bold red]✗ License invalid: {msg}[/bold red]\n\n"
            "  Your 30‑day license may have expired. Renew by holding 100,000 $AEGIS tokens\n"
            "  and visiting: [link]https://yourusername.github.io/aegis-license/[/link]\n\n"
            "  After renewal, run: [bold]aegis activate YOUR_NEW_LICENSE_KEY[/bold]\n"
        )
        sys.exit(1)

    # Optional: show remaining days if expiry is present
    if ":" in license_key:
        try:
            expiry = int(license_key.split(":")[1])
            remaining = expiry - int(time.time())
            if remaining > 0:
                days = remaining // 86400
                console.print(f"[dim]License expires in {days} days[/dim]")
        except (ValueError, IndexError):
            pass