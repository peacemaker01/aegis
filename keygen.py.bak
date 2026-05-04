#!/usr/bin/env python3
"""
Aegis — Seller Key Management Tool
===================================
Run this ONCE to generate your Ed25519 keypair.
Then use it to sign license keys for buyers.

NEVER commit your private key to any repository.

Usage:
  python keygen.py generate                → create keypair, save to keys/
  python keygen.py sign <device_id>        → sign a buyer's device ID (permanent license)
  python keygen.py sign <device_id> --expiry 30 → sign a 30‑day license
  python keygen.py verify <device_id> <license_key>
"""
import sys
import json
import time
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


def cmd_sign(device_id: str, expiry_days: int = None):
    """
    Sign a buyer's device_id and print their license key.
    If expiry_days is provided, creates a time‑limited license.
    """
    priv_path = KEYS_DIR / "private.pem"
    if not priv_path.exists():
        console.print("[red]✗ keys/private.pem not found. Run: python keygen.py generate[/red]")
        sys.exit(1)

    priv_pem = priv_path.read_text()
    
    if expiry_days:
        expiry = int(time.time()) + expiry_days * 86400
        license_key = sign_license(device_id, priv_pem, expiry=expiry)
        expiry_str = time.ctime(expiry)
        console.print(Panel(
            f"Device ID:   [bold cyan]{device_id}[/bold cyan]\n\n"
            f"License Key (valid for {expiry_days} days until {expiry_str}):\n"
            f"[bold green]{license_key}[/bold green]\n\n"
            f"Send this key to the buyer via Whop/Gumroad email.",
            title="Time‑Limited License Key Generated",
            border_style="green",
        ))
    else:
        license_key = sign_license(device_id, priv_pem)
        console.print(Panel(
            f"Device ID:   [bold cyan]{device_id}[/bold cyan]\n\n"
            f"License Key (permanent):\n[bold green]{license_key}[/bold green]\n\n"
            f"Send this key to the buyer via Whop/Gumroad email.",
            title="Permanent License Key Generated",
            border_style="green",
        ))


def cmd_verify(device_id: str, license_key: str):
    """Verify that a license key is valid for a given device_id."""
    pub_path = KEYS_DIR / "public.pem"
    if not pub_path.exists():
        console.print("[red]✗ keys/public.pem not found.[/red]")
        sys.exit(1)

    import base64
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.exceptions import InvalidSignature
    
    pub_key = load_pem_public_key(pub_path.read_bytes())
    
    # Check if license key has expiry format
    if ":" in license_key:
        sig_part, expiry_str = license_key.rsplit(":", 1)
        try:
            expiry = int(expiry_str)
            message = f"{device_id}:{expiry}".encode()
        except ValueError:
            console.print("[red]✗ Invalid license key format[/red]")
            return
    else:
        sig_part = license_key
        message = device_id.encode()
    
    try:
        padding = "=" * ((4 - len(sig_part) % 4) % 4)
        sig = base64.urlsafe_b64decode(sig_part + padding)
        pub_key.verify(sig, message)
        if ":" in license_key:
            import time
            remaining = expiry - int(time.time())
            if remaining > 0:
                days = remaining // 86400
                console.print(f"[green bold]✓ VALID[/green bold] — License valid for {days} more days")
            else:
                console.print("[yellow]⚠ License has expired[/yellow]")
        else:
            console.print("[green bold]✓ VALID[/green bold] — Key is authentic (permanent)")
    except InvalidSignature:
        console.print("[red bold]✗ INVALID[/red bold] — Signature does not match")
    except Exception as e:
        console.print(f"[red bold]✗ INVALID[/red bold] — {e}")


def cmd_my_device():
    """Print this machine's device ID (for testing)."""
    did = get_device_id()
    console.print(f"This device's ID: [bold cyan]{did}[/bold cyan]")


def parse_args():
    """Parse command line arguments with optional --expiry flag."""
    args = sys.argv[1:]
    if not args:
        return None, None, None
    
    cmd = args[0]
    if cmd == "sign":
        # Check for --expiry flag
        expiry_days = None
        device_id = None
        i = 1
        while i < len(args):
            if args[i] == "--expiry" and i + 1 < len(args):
                try:
                    expiry_days = int(args[i + 1])
                    i += 2
                except ValueError:
                    console.print("[red]Invalid expiry value[/red]")
                    sys.exit(1)
            elif args[i].startswith("--"):
                console.print(f"[red]Unknown option: {args[i]}[/red]")
                sys.exit(1)
            else:
                device_id = args[i]
                i += 1
        return cmd, device_id, expiry_days
    else:
        return cmd, args[1] if len(args) > 1 else None, None


COMMANDS = {
    "generate":  (cmd_generate, 0),
    "verify":    (cmd_verify, 2),
    "device-id": (cmd_my_device, 0),
}

if __name__ == "__main__":
    cmd, arg, expiry = parse_args()
    
    if cmd is None:
        console.print(
            "[bold]Usage:[/bold]\n"
            "  python keygen.py generate\n"
            "  python keygen.py sign <device_id> [--expiry DAYS]\n"
            "  python keygen.py verify <device_id> <license_key>\n"
            "  python keygen.py device-id\n"
        )
        sys.exit(1)
    
    if cmd == "sign":
        if not arg:
            console.print("[red]Missing device_id for 'sign'[/red]")
            sys.exit(1)
        cmd_sign(arg, expiry)
    elif cmd in COMMANDS:
        func, nargs = COMMANDS[cmd]
        if cmd == "verify":
            if not arg:
                console.print("[red]Missing device_id for 'verify'[/red]")
                sys.exit(1)
            license_key = sys.argv[3] if len(sys.argv) > 3 else None
            if not license_key:
                console.print("[red]Missing license_key for 'verify'[/red]")
                sys.exit(1)
            func(arg, license_key)
        else:
            func()
    else:
        console.print(f"[red]Unknown command: {cmd}[/red]")
        sys.exit(1)