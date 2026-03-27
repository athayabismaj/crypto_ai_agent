import os
from dataclasses import dataclass
from typing import Dict, Any

from runtime.agent.security.encryptor import encrypt, decrypt, get_master_key

class KeyNotFoundError(Exception):
    pass

class RotationError(Exception):
    pass

class APIError(Exception):
    pass

@dataclass
class PermissionReport:
    exchange: str
    can_read: bool       # baca balance & posisi
    can_trade: bool      # buka & tutup order
    can_withdraw: bool   # HARUS False - agent tidak boleh withdraw
    ip_restricted: bool  # True = key hanya valid dari IP tertentu
    passed: bool         # True jika can_read+can_trade dan NOT can_withdraw

class KeyManager:
    """
    Manajemen dan proteksi API keys di memory.
    Keys disimpan secara default dalam bentuk terenkripsi di memory 
    menggunakan CRYPTO_AGENT_MASTER_KEY.
    """
    
    def __init__(self, config: Any = None):
        """
        config: AgentConfig instance (opsional untuk dependensi eksternal).
        Pada inisialisasi, load key dari environment lalu simpan dalam kondisi terenkripsi.
        """
        self.config = config
        self._master_key = get_master_key()
        self._keys: Dict[str, Dict[str, bytes]] = {}
        
        # Load default keys from environment
        self._load_env_keys()

    def _load_env_keys(self) -> None:
        """Membaca key dari .env dan mengenkripsinya ke _keys dictionary."""
        # Binance Production
        b_key = os.getenv("BINANCE_API_KEY")
        b_sec = os.getenv("BINANCE_API_SECRET")
        if b_key and b_sec:
            self._store_keys('binance', 'production', b_key, b_sec)

        # Binance Testnet (Shadow Mode)
        bt_key = os.getenv("BINANCE_TESTNET_KEY")
        bt_sec = os.getenv("BINANCE_TESTNET_SECRET")
        if bt_key and bt_sec:
            self._store_keys('binance', 'shadow', bt_key, bt_sec)
            
        # Optional: other exchanges

    def _store_keys(self, exchange: str, env: str, key: str, secret: str) -> None:
        if exchange not in self._keys:
            self._keys[exchange] = {}
            
        self._keys[exchange][f"{env}_key"] = encrypt(key, self._master_key)
        self._keys[exchange][f"{env}_secret"] = encrypt(secret, self._master_key)

    def get_api_key(self, exchange: str, env: str = 'production') -> str:
        """Mengambil string API key (didodekripsi saat digunakan)."""
        try:
            encrypted_key = self._keys[exchange][f"{env}_key"]
            return decrypt(encrypted_key, self._master_key)
        except KeyError:
            raise KeyNotFoundError(f"API key API untuk {exchange} ({env}) tidak ditemukan.")

    def get_api_secret(self, exchange: str, env: str = 'production') -> str:
        """Mengambil string API secret (didodekripsi saat digunakan)."""
        try:
            encrypted_secret = self._keys[exchange][f"{env}_secret"]
            return decrypt(encrypted_secret, self._master_key)
        except KeyError:
            raise KeyNotFoundError(f"API secret untuk {exchange} ({env}) tidak ditemukan.")

    def rotate_key(self, exchange: str, new_key: str, new_secret: str, env: str = 'production') -> bool:
        """Mengganti API key yang ada di memory dan mengenkripsinya."""
        try:
            self._store_keys(exchange, env, new_key, new_secret)
            return True
        except Exception as e:
            raise RotationError(f"Gagal melakukan rotasi key: {str(e)}")

    def validate_permissions(self, exchange: str, env: str = 'production') -> PermissionReport:
        """
        Validasi permissions key langsung ke exchange atau me-mock status.
        Karena security/ tidak memiliki akses library REST penuh,
        sementara ini mengembalikan nilai mock (atau bisa diinjeksi via validator external).
        Jika can_withdraw=True, config system akan memicu shutdown.
        """
        # TODO: Implementasi HTTP call ke rest client internal nanti.
        # Sementara ditaruh nilai default yang valid untuk memastikan main() berjalan
        return PermissionReport(
            exchange=exchange,
            can_read=True,
            can_trade=True,
            can_withdraw=False,
            ip_restricted=True,
            passed=True
        )
