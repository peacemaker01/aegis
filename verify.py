import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

pub_pem = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAwQ7SCM2zfjzIutd5xwmpXRUKAqeO3Og61mO1xs//WYQ=
-----END PUBLIC KEY-----"""
device_id = "129474d0bb4e8ddc"
license_key = "D3y90AP8g-aPbTwRc4dZ2Rjw0Oa0rsoFAMhxzrQfLiSihOzlZsui9A72zEF50VQr8wgxuO-SCJDMhlM8qz6JDg=="

pub_key = load_pem_public_key(pub_pem)
sig = base64.urlsafe_b64decode(license_key)
try:
    pub_key.verify(sig, device_id.encode())
    print("✓ Signature is valid")
except Exception as e:
    print(f"✗ Invalid: {e}")
