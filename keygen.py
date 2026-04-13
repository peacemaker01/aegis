#!/usr/bin/env python3
"""
Aegis — Seller Key Management Tool
===================================
Run this ONCE to generate your Ed25519 keypair.
Then use it to sign license keys for buyers.

NEVER commit your private key to any repository.

Usage:
  python keygen.py generate           → create keypair, save to keys/
  python keygen.py sign <device_id>   → sign a buyer's device ID
  python keygen.py verify <device_id> <license_key>
"""
import sys
import json
from pathlib import Path

# Always run from project root
sys.path.insert(0, str(Path(__file__).parent))

from core.license import generate_keypair, sign_license, validate_license, get_device_id
from rich.console import Console
from rich.panel import Panel

console = Console()
KEYS_DIR = Path("keys")


def cmd_generate():
    """Generate a fresh keypair and save to keys/ directory."""
    if KEYS_DIR.exists() and (KEYS_DIR / "private.pem").exists():
        console.print("[yellow]⚠ keys/private.pem already exists.[/yellow]")
        if input("Overwrite? (yes/no): ").strip().lower() != "yes":
            console.print("Aborted.")
            return

    KEYS_DIR.mkdir(exist_ok=True)
    priv_pem, pub_pem = generate_keypair()

    (KEYS_DIR / "private.pem").write_text(priv_pem)
    (KEYS_DIR / "public.pem").write_text(pub_pem)

    # Protect private key file permissions (Unix)
    try:
        (KEYS_DIR / "private.pem").chmod(0o600)
    except Exception:
        pass

    console.print(Panel(
        f"[green bold]✓ Keypair generated[/green bold]\n\n"
        f"  Private key: [bold]keys/private.pem[/bold]  ← KEEP SECRET\n"
        f"  Public key:  [bold]keys/public.pem[/bold]\n\n"
        f"[yellow]Next step:[/yellow]\n"
        f"  Copy the contents of [bold]keys/public.pem[/bold] into\n"
        f"  [bold]core/license.py → BAKED_PUBLIC_KEY_PEM[/bold]\n"
        f"  Then rebuild the binary.",
        title="Key Generation Complete",
        border_style="green",
    ))

    console.print("\n[bold]Public key (paste into core/license.py):[/bold]")
    console.print(pub_pem)


def cmd_sign(device_id: str):
    """Sign a buyer's device_id and print their license key."""
    priv_path = KEYS_DIR / "private.pem"
    if not priv_path.exists():
        console.print("[red]✗ keys/private.pem not found. Run: python keygen.py generate[/red]")
        sys.exit(1)

    priv_pem    = priv_path.read_text()
    license_key = sign_license(device_id, priv_pem)

    console.print(Panel(
        f"Device ID:   [bold cyan]{device_id}[/bold cyan]\n\n"
        f"License Key:\n[bold green]{license_key}[/bold green]\n\n"
        f"Send this key to the buyer via Whop/Gumroad email.",
        title="License Key Generated",
        border_style="green",
    ))


def cmd_verify(device_id: str, license_key: str):
    """Verify that a license key is valid for a given device_id."""
    # Temporarily patch the validate function to use our public key
    pub_path = KEYS_DIR / "public.pem"
    if not pub_path.exists():
        console.print("[red]✗ keys/public.pem not found.[/red]")
        sys.exit(1)

    import base64
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    pub_key = load_pem_public_key(pub_path.read_bytes())
    try:
        sig = base64.urlsafe_b64decode(license_key.encode())
        pub_key.verify(sig, device_id.encode())
        console.print(f"[green bold]✓ VALID[/green bold] — key is authentic for device {device_id}")
    except Exception:
        console.print(f"[red bold]✗ INVALID[/red bold] — key does not match device {device_id}")


def cmd_my_device():
    """Print this machine's device ID (for testing)."""
    did = get_device_id()
    console.print(f"This device's ID: [bold cyan]{did}[/bold cyan]")


COMMANDS = {
    "generate":  (cmd_generate,  0),
    "sign":      (cmd_sign,      1),
    "verify":    (cmd_verify,    2),
    "device-id": (cmd_my_device, 0),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        console.print(
            "[bold]Usage:[/bold]\n"
            "  python keygen.py generate\n"
            "  python keygen.py sign <device_id>\n"
            "  python keygen.py verify <device_id> <license_key>\n"
            "  python keygen.py device-id\n"
        )
        sys.exit(1)

    cmd, nargs = COMMANDS[sys.argv[1]]
    args = sys.argv[2:]
    if len(args) < nargs:
        console.print(f"[red]Missing arguments for '{sys.argv[1]}'[/red]")
        sys.exit(1)
    cmd(*args[:nargs])
