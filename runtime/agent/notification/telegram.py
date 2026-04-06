"""
telegram.py — Pengiriman Telegram (Bot API)
Mengirim notifikasi HTML Telegram secara asinkron dengan fitur exponential retry.
"""

import asyncio
import os
import textwrap

import aiohttp

from runtime.shared.utils.helpers import retry_async
from runtime.shared.utils.logger import get_logger

log = get_logger(__name__)


class TelegramNotifier:
    MAX_MESSAGE_LEN = 4096  # Telegram API hard limit
    MAX_RETRY = 3
    RETRY_DELAY_S = 2.0

    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.default_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage" if self.bot_token else ""

    def is_configured(self) -> bool:
        """True jika BOT_TOKEN dan CHAT_ID tersedia."""
        return bool(self.bot_token and self.default_chat_id)

    async def send(self, message: str, chat_id: str = None, parse_mode: str = 'HTML', silent: bool = False) -> bool:
        """
        Kirim pesan ke Telegram. Return True jika berhasil.
        Dipanggil via retry_async.
        """
        if not self.is_configured():
            log.debug("[MOCK TELEGRAM] " + message.replace('\n', ' '))
            return True

        target_chat = chat_id or self.default_chat_id
        if not target_chat:
            return False

        # Split jika pesan terlalu panjang -> hindari error 400
        if len(message) > self.MAX_MESSAGE_LEN:
            log.warning("Pesan telegram lebih dari 4096 karakter. Melakukan split otomatis.")
            chunks = textwrap.wrap(message, self.MAX_MESSAGE_LEN, break_long_words=False, replace_whitespace=False)
            success = True
            for chunk in chunks:
                ok = await self._send_chunk(chunk, target_chat, parse_mode, silent)
                if not ok:
                    success = False
            return success
            
        return await self._send_chunk(message, target_chat, parse_mode, silent)
        
    async def _send_chunk(self, chunk: str, chat_id: str, parse_mode: str, silent: bool) -> bool:
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_notification": silent,
            "disable_web_page_preview": True
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.base_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return True
                else:
                    err = await resp.text()
                    log.error(f"Telegram API Error {resp.status}: {err}")
                    raise RuntimeError(f"HTTP {resp.status} dari Telegram")

    async def send_with_retry(self, message: str, **kwargs: Any) -> bool:
        """Wrapper dengan exponential backoff retry."""
        try:
            return await retry_async(
                self.send, 
                max_attempts=self.MAX_RETRY, 
                base_delay_s=self.RETRY_DELAY_S, 
                message=message, 
                **kwargs
            )
        except Exception as e:
            log.error(f"Gagal push ke Telegram setelah {self.MAX_RETRY} retry: {e}")
            return False
