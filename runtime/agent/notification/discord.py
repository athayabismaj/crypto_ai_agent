"""
discord.py — Pengiriman Discord
Implementasi webhook Discord untuk peringatan alert serius (WARNING & CRITICAL).
"""

import os
from typing import Any

import aiohttp

from runtime.shared.utils.helpers import retry_async
from runtime.shared.utils.logger import get_logger
from runtime.shared.utils.time_utils import utcnow

log = get_logger(__name__)


class DiscordNotifier:
    MAX_EMBED_DESCRIPTION = 4096

    EMBED_COLORS = {
        "info": 0x2980B9,  # biru
        "warning": 0xF39C12,  # oranye
        "critical": 0xE74C3C,  # merah
        "success": 0x27AE60,  # hijau
    }

    def __init__(self):
        self.webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    def is_configured(self) -> bool:
        """True jika DISCORD_WEBHOOK_URL tersedia."""
        return bool(self.webhook_url)

    async def _send_embed(self, payload: dict[str, Any], webhook_url: str) -> bool:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status in (200, 204):
                    return True
                else:
                    err = await resp.text()
                    log.error(f"Discord Webhook Error HTTP {resp.status}: {err}")
                    raise RuntimeError(f"HTTP {resp.status} dari Discord Webhook")

    async def send(
        self,
        title: str,
        description: str,
        severity: str = "info",
        fields: list[dict] = None,
        webhook_url: str = None,
    ) -> bool:
        """Kirim Discord embed. Digunakan retry_async untuk exponential backoff."""
        if not self.is_configured():
            log.debug(f"[MOCK DISCORD] {severity.upper()} - {title}")
            return True

        target_url = webhook_url or self.webhook_url
        if not target_url:
            return False

        color = self.EMBED_COLORS.get(severity.lower(), 0x95A5A6)

        # Clip desc
        if len(description) > self.MAX_EMBED_DESCRIPTION:
            description = description[: self.MAX_EMBED_DESCRIPTION - 3] + "..."

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                    "fields": fields or [],
                    "timestamp": utcnow().isoformat(),
                    "footer": {"text": "crypto_ai_agent"},
                }
            ]
        }

        try:
            return await retry_async(
                self._send_embed,
                max_attempts=3,
                base_delay_s=2.0,
                payload=payload,
                webhook_url=target_url,
            )
        except Exception as e:
            log.error(f"Gagal push ke Discord Webhook sesudah retry: {e}")
            return False
