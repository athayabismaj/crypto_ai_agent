"""
Lapis 1: PreTradeCheck — Validator Data & Market
==================================================
Gate paling pertama sebelum risk evaluation. Jika data tidak valid
atau market dalam kondisi abnormal, tidak perlu melanjutkan ke layer
risk yang lebih dalam.

Filosofi:
    Fast-fail — berhenti di check pertama yang gagal (validate).
    Full-scan — jalankan semua check untuk debugging (validate_all).
    Spread check terpisah karena hanya menghasilkan WARNED, bukan BLOCKED.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore

logger = logging.getLogger(__name__)

ORDER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,36}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_client_order_id(cid: str) -> tuple[bool, str]:
    """Validasi format client_order_id sesuai aturan exchange."""
    if not cid:
        return False, "client_order_id kosong"
    if len(cid) > 36:
        return False, f"client_order_id terlalu panjang: {len(cid)} > 36"
    if not ORDER_ID_PATTERN.match(cid):
        return False, f"client_order_id mengandung karakter tidak valid: {cid}"
    return True, ""


def generate_order_id(strategy_id: str, symbol: str) -> str:
    """
    Generate client_order_id yang valid untuk exchange.

    Format: {strategy_id}_{symbol}_{timestamp_ms}
    Contoh: spot_v1_BTCUSDT_1731658200000
    Truncate ke 36 karakter maks.
    """
    ts = int(_utcnow().timestamp() * 1000)
    raw = f"{strategy_id}_{symbol}_{ts}"
    return raw[:36].replace(" ", "_")


class PreTradeCheck:
    """
    Validator data dan kondisi market sebelum evaluasi risk.

    Semua check di sini adalah fast-fail (O(1)) — tidak ada kalkulasi berat.
    Setiap kegagalan menghasilkan error code yang bisa di-audit.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.allow_market_order: bool = getattr(config, "allow_market_order", True)
        self.require_client_order_id: bool = getattr(config, "require_client_order_id", True)
        self.max_spread_pct: float = getattr(config, "max_spread_pct", 0.01)  # Default 1%
        self.min_qty: float = getattr(config, "min_qty", 1e-8)

    def validate(self, request: TradeRequest, portfolio_state: dict) -> tuple[bool, str]:
        """
        Fast-fail validation check. Return (True, '') if OK.

        Berhenti di check pertama yang gagal.
        Untuk full-scan (semua check), gunakan validate_all().
        """
        # 1. Symbol format
        if not request.symbol or len(request.symbol) > 20:
            return False, "INVALID_SYMBOL: Format symbol salah"

        # 2. Side
        if request.side not in ("BUY", "SELL"):
            return False, "INVALID_SIDE: Side harus BUY atau SELL"

        # 3. Quantity minimum
        if request.quantity < self.min_qty:
            return False, f"INVALID_QTY: Quantity di bawah minimum {self.min_qty}"

        # 4. Price & Market order flag
        if request.price < 0:
            return False, "INVALID_PRICE: Price tidak boleh negatif"
        if request.price == 0.0 and not self.allow_market_order:
            return False, "MARKET_ORDER_DISABLED: Market order dicegah oleh sistem"

        # 5. Client Order ID
        if self.require_client_order_id:
            ok, msg = validate_client_order_id(request.client_order_id)
            if not ok:
                return False, f"INVALID_ORDER_ID: {msg}"

        # 6. Exchange Status
        exchange_status = portfolio_state.get("exchange_status", "normal")
        if exchange_status != "normal":
            return False, f"EXCHANGE_MAINTENANCE: Status {exchange_status}"

        # 7. Market Halted
        market_halted = portfolio_state.get("market_halted", False)
        if market_halted:
            return False, "MARKET_HALTED"

        # 8. Harga tersedia
        last_price_key = f"last_price_{request.symbol}"
        if last_price_key not in portfolio_state:
            return False, f"PRICE_UNAVAILABLE: Tidak ada harga untuk {request.symbol}"

        # Lolos semua critical check
        return True, ""

    def validate_all(
        self, request: TradeRequest, portfolio_state: dict
    ) -> list[tuple[str, bool, str]]:
        """
        Jalankan SEMUA check tanpa fast-fail.

        Return list (check_name, passed, message).
        Berguna untuk debugging dan testing.
        """
        results: list[tuple[str, bool, str]] = []

        # 1. Symbol
        ok = bool(request.symbol and len(request.symbol) <= 20)
        results.append(("symbol_format", ok, "" if ok else "Format symbol salah"))

        # 2. Side
        ok = request.side in ("BUY", "SELL")
        results.append(("side", ok, "" if ok else f"Side invalid: {request.side}"))

        # 3. Quantity
        ok = request.quantity >= self.min_qty
        results.append(
            ("quantity", ok, "" if ok else f"Qty {request.quantity} < min {self.min_qty}")
        )

        # 4. Price
        if request.price < 0:
            results.append(("price", False, "Price negatif"))
        elif request.price == 0.0 and not self.allow_market_order:
            results.append(("price", False, "Market order disabled"))
        else:
            results.append(("price", True, ""))

        # 5. Client Order ID
        if self.require_client_order_id:
            ok, msg = validate_client_order_id(request.client_order_id)
            results.append(("client_order_id", ok, msg))
        else:
            results.append(("client_order_id", True, "Not required"))

        # 6. Exchange status
        status = portfolio_state.get("exchange_status", "normal")
        ok = status == "normal"
        results.append(("exchange_status", ok, "" if ok else f"Status: {status}"))

        # 7. Market halted
        halted = portfolio_state.get("market_halted", False)
        results.append(("market_halted", not halted, "" if not halted else "Market halted"))

        # 8. Price available
        has_price = f"last_price_{request.symbol}" in portfolio_state
        results.append(("price_available", has_price, "" if has_price else "Price unavailable"))

        return results

    def check_spread(self, request: TradeRequest, portfolio_state: dict) -> list[str]:
        """Check terpisah karena spread hanya memunculkan WARNED, bukan BLOCKED."""
        spread_key = f"spread_pct_{request.symbol}"
        spread_pct = portfolio_state.get(spread_key, 0.0)
        if spread_pct > self.max_spread_pct:
            return [
                f"WIDE_SPREAD: {request.symbol} spread {spread_pct:.2%} > limit {self.max_spread_pct:.2%}"
            ]
        return []
