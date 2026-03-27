import pytest
import os
from unittest.mock import patch
from runtime.agent.security.key_manager import KeyManager, KeyNotFoundError, RotationError, PermissionReport

@pytest.fixture
def master_key():
    from runtime.agent.security.encryptor import generate_key
    return generate_key().decode('utf-8')

def test_key_manager_init(master_key):
    env_vars = {
        "CRYPTO_AGENT_MASTER_KEY": master_key,
        "BINANCE_API_KEY": "livekey",
        "BINANCE_API_SECRET": "livesecret",
        "BINANCE_TESTNET_KEY": "testkey",
        "BINANCE_TESTNET_SECRET": "testsecret",
    }
    with patch.dict(os.environ, env_vars):
        km = KeyManager()
        assert km.get_api_key('binance', 'production') == "livekey"
        assert km.get_api_secret('binance', 'production') == "livesecret"
        assert km.get_api_key('binance', 'shadow') == "testkey"
        assert km.get_api_secret('binance', 'shadow') == "testsecret"

def test_key_manager_missing_keys(master_key):
    with patch.dict(os.environ, {"CRYPTO_AGENT_MASTER_KEY": master_key}, clear=True):
        km = KeyManager()
        with pytest.raises(KeyNotFoundError):
            km.get_api_key('binance', 'production')
        with pytest.raises(KeyNotFoundError):
            km.get_api_secret('binance', 'production')

def test_rotate_key(master_key):
    with patch.dict(os.environ, {"CRYPTO_AGENT_MASTER_KEY": master_key}, clear=True):
        km = KeyManager()
        
        # Test rotating a new key
        res = km.rotate_key('binance', 'new_key', 'new_secret', 'production')
        assert res is True
        
        assert km.get_api_key('binance', 'production') == 'new_key'
        assert km.get_api_secret('binance', 'production') == 'new_secret'

def test_rotate_key_error(master_key):
    with patch.dict(os.environ, {"CRYPTO_AGENT_MASTER_KEY": master_key}, clear=True):
        km = KeyManager()
        with patch.object(km, '_store_keys', side_effect=Exception("Storage Error")):
            with pytest.raises(RotationError, match="Gagal melakukan rotasi key"):
                km.rotate_key('binance', 'k', 's', 'production')

def test_validate_permissions(master_key):
    with patch.dict(os.environ, {"CRYPTO_AGENT_MASTER_KEY": master_key}, clear=True):
        km = KeyManager()
        perms = km.validate_permissions('binance')
        assert isinstance(perms, PermissionReport)
        assert perms.can_withdraw is False
        assert perms.passed is True
