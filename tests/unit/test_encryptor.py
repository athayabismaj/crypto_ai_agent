import os
from unittest.mock import patch

import pytest  # type: ignore

from runtime.agent.security.encryptor import (  # type: ignore
    decrypt,
    encrypt,
    generate_key,
    get_master_key,
    hash_order_id,
)


def test_generate_key():
    key = generate_key()
    assert len(key) == 44  # 32 bytes urlsafe base64 is 44 chars


def test_encrypt_decrypt():
    key = generate_key()
    data = "mysecretdata"

    enc = encrypt(data, key)
    assert enc != data.encode("utf-8")

    dec = decrypt(enc, key)
    assert dec == data


def test_encrypt_bytes():
    key = generate_key()
    data = b"bytesdata"

    enc = encrypt(data, key)
    assert decrypt(enc, key) == "bytesdata"


def test_get_master_key():
    with patch.dict(os.environ, {"CRYPTO_AGENT_MASTER_KEY": "validkey"}):
        assert get_master_key() == b"validkey"


def test_get_master_key_missing():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="belum diset"):
            get_master_key()


def test_hash_order_id():
    h1 = hash_order_id("order123")
    h2 = hash_order_id("order123")
    assert h1 == h2
    assert len(h1) == 64
