import hashlib
import os

from cryptography.fernet import Fernet  # type: ignore


def generate_key() -> bytes:
    """
    Menghasilkan 32-byte (Url-safe base64) Fernet key.
    Gunakan di REPL untuk meng-generate CRYPTO_AGENT_MASTER_KEY di .env
    """
    return Fernet.generate_key()


def get_master_key() -> bytes:
    """Mengambil master key dari env. Jika belum diisi, raise error."""
    key = os.getenv("CRYPTO_AGENT_MASTER_KEY")
    if not key:
        raise ValueError("CRYPTO_AGENT_MASTER_KEY belum diset dalam environment variables.")
    return key.encode("utf-8")


def encrypt(data: str | bytes, key: bytes) -> bytes:
    """Mengenkripsi data menggunakan AES-128-CBC + HMAC-SHA256 (Fernet)."""
    f = Fernet(key)
    if isinstance(data, str):
        data = data.encode("utf-8")
    return f.encrypt(data)


def decrypt(ciphertext: bytes, key: bytes) -> str:
    """Mendekripsi data kembali ke string."""
    f = Fernet(key)
    decrypted_bytes = f.decrypt(ciphertext)
    return decrypted_bytes.decode("utf-8")


def hash_order_id(client_order_id: str) -> str:
    """Hash SHA-256 untuk logging order ID dengan aman."""
    hash_obj = hashlib.sha256(client_order_id.encode("utf-8"))
    return hash_obj.hexdigest()
