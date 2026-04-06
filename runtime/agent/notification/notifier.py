"""
notifier.py — Router Terpusat
Satu-satunya titik persinggungan modul lain untuk melontarkan pesan keluar.
Mengatur routing dan pendelegasian channel sesuai event_type.
"""

import asyncio
from typing import Any

from runtime.agent.notification.discord import DiscordNotifier
from runtime.agent.notification.telegram import TelegramNotifier
from runtime.shared.utils.logger import get_logger

log = get_logger(__name__)


class Notifier:
    def __init__(
        self,
        telegram: TelegramNotifier,
        discord: DiscordNotifier,
        config: Any,
    ):
        self._telegram = telegram
        self._discord = discord
        self._config = config

    def _should_notify_discord(self, event_type: str, severity: str) -> bool:
        """Discord eksklusif untuk level tinggi."""
        always_discord = {"trade_closed_loss", "circuit_breaker", "strategy_disabled", "system_error", "reconcile_issue"}
        if event_type in always_discord:
            return True
        if severity in ("warning", "critical"):
            return True
        return False

    def format_message(self, event_type: str, data: dict) -> str:
        """Delegasi format per event type untuk Telegram HTML."""
        if event_type == "trade_opened":
             return (f"🟢 <b>Trade Dibuka</b>\n"
                     f"Simbol : <code>{data.get('symbol')}</code>\n"
                     f"Side : {data.get('side')} | Qty: {data.get('qty')}\n"
                     f"Entry : {data.get('avg_fill_price')}\n"
                     f"SL : {data.get('sl_price')} | TP: {data.get('tp_price')}\n"
                     f"Risk : ${data.get('risk_usd', 0):.2f}\n"
                     f"Mode : [{data.get('mode', 'UNKNOWN')}]")
        elif event_type == "trade_closed_win":
             return (f"✅ <b>Trade Ditutup Profit</b>\n"
                     f"Simbol: <code>{data.get('symbol')}</code>\n"
                     f"PnL: +${data.get('pnl_usd', 0):.2f} (+{data.get('pnl_pct', 0):.2f}%)\n"
                     f"Exit: {data.get('exit_reason')}\n"
                     f"Equity: ${data.get('equity', 0):.2f}")
        elif event_type == "trade_closed_loss":
             return (f"❌ <b>Trade Ditutup Loss</b>\n"
                     f"Simbol: <code>{data.get('symbol')}</code>\n"
                     f"PnL: -${abs(data.get('pnl_usd', 0)):.2f} ({data.get('pnl_pct', 0):.2f}%)\n"
                     f"Exit: {data.get('exit_reason')}\n"
                     f"Equity: ${data.get('equity', 0):.2f}")
        elif event_type == "circuit_breaker":
             return (f"🚨 <b>CIRCUIT BREAKER</b>\n"
                     f"Status: {data.get('state')}\n"
                     f"Alasan: {data.get('reasons')}\n"
                     f"Daily PnL: ${data.get('daily_pnl', 0):.2f}\n"
                     f"Equity: ${data.get('equity', 0):.2f}\n"
                     f"Halt: {data.get('halt_duration')}")
        elif event_type == "strategy_disabled":
             return (f"⚠️ <b>Strategi Dinonaktifkan</b>\n"
                     f"ID: <code>{data.get('strategy_id')}</code>\n"
                     f"Alasan: {data.get('reasons')}\n"
                     f"WR: {data.get('win_rate', 0)*100:.1f}% | Sharpe: {data.get('sharpe', 0)}")
        elif event_type == "system_error":
             return (f"🔴 <b>SYSTEM ERROR</b>\n"
                     f"Komponen: {data.get('component')}\n"
                     f"Pesan: {data.get('message')}\n"
                     f"Waktu: {data.get('timestamp')}")
        elif event_type == "daily_summary":
             return (f"📊 <b>Summary Harian</b>\n"
                     f"PnL: ${data.get('daily_pnl', 0):.2f}\n"
                     f"Trades: {data.get('total_trades')}\n"
                     f"WR: {data.get('win_rate', 0)*100:.1f}%\n"
                     f"Equity: ${data.get('equity', 0):.2f}\n"
                     f"Posisi: {data.get('open_count')}")
                     
        # Generic formatter fallthrough
        body = "\n".join([f"{k}: {v}" for k, v in data.items()])
        return f"🔔 <b>{event_type.upper()}</b>\n{body}"

    async def notify(self, event_type: str, data: dict, severity: str = 'info') -> None:
        """
        Entry point utama. Gagal kirim notifikasi tidak boleh throw panic ke pemanggil.
        """
        try:
            msg = self.format_message(event_type, data)
            
            # 1. Fire to Telegram asinkron
            asyncio.create_task(self._telegram.send_with_retry(msg, parse_mode='HTML'))
            
            # 2. Fire to Discord if eligible
            if self._should_notify_discord(event_type, severity):
                title = f"{event_type.replace('_', ' ').title()}"
                
                fields = []
                for k, v in data.items():
                    fields.append({"name": str(k), "value": str(v), "inline": True})
                    
                asyncio.create_task(
                    self._discord.send(
                        title=title, 
                        description=f"Status [{severity.upper()}] dikirim dari Notifier Engine.",
                        severity=severity,
                        fields=fields
                    )
                )
        except Exception as e:
            log.error(f"Gagal melakukan dispatch notifikasi untuk {event_type}. Err: {e}")

    async def send_emergency(self, title: str, message: str, data: dict = None) -> None:
        """
        Dipanggil oleh safe_mode.emergency_stop() saat terjadi krismon/keputungan API masif.
        Akan DITUNGGU (await) bukan fire and forget agar benar-benar terkirim!
        """
        try:
            tg_msg = f"🔴 <b>EMERGENCY STOP: {title}</b>\n{message}\n"
            if data:
                tg_msg += "\n" + "\n".join([f"{k}: {v}" for k,v in data.items()])
                
            promises = [
                 self._telegram.send_with_retry(tg_msg),
                 self._discord.send(
                    title=f"EMERGENCY: {title}",
                    description=message,
                    severity="critical"
                 )
            ]
            
            results = await asyncio.gather(*promises, return_exceptions=True)
            if all(isinstance(r, Exception) or r is False for r in results):
                log.critical("SELURUH KANAL NOTIFIKASI EMERGENCY GAGAL. SISTEM MURNI BUTA.")
        except Exception as e:
            log.critical(f"FATAL: send_emergency internal router crash. {e}")
