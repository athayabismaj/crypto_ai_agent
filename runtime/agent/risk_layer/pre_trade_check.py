"""
Lapis 1: PreTradeCheck
Validasi awal data dan format agar tidak perlu lanjut ke layer berikutnya jika gagal.
"""

import re

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore

ORDER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,36}$")


def validate_client_order_id(cid: str) -> tuple[bool, str]:
    if not cid:
        return False, "client_order_id kosong"
    if len(cid) > 36:
        return False, f"client_order_id terlalu panjang: {len(cid)} > 36"
    if not ORDER_ID_PATTERN.match(cid):
        return False, f"client_order_id mengandung karakter tidak valid: {cid}"
    return True, ""


class PreTradeCheck:
    def __init__(self, config: AgentConfig):
        # Gunakan dict fallback krn getattr bisa melempar attribut error jika tak ada di AgentConfig (Pydantic V2)
        # Atau langsung property jika sudah ada.
        self.allow_market_order = getattr(config, "allow_market_order", True)
        self.require_client_order_id = getattr(config, "require_client_order_id", True)
        self.max_spread_pct = getattr(config, "max_spread_pct", 0.01)  # Default 1%
        self.min_qty = getattr(config, "min_qty", 1e-8)

    def validate(self, request: TradeRequest, portfolio_state: dict) -> tuple[bool, str]:
        """
        Fast-fail validation check. Return (True, '') if OK.
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

    def check_spread(self, request: TradeRequest, portfolio_state: dict) -> list[str]:
        """Check terpisah karena spread hanya memunculkan WARNED, bukan BLOCKED."""
        spread_key = f"spread_pct_{request.symbol}"
        spread_pct = portfolio_state.get(spread_key, 0.0)
        if spread_pct > self.max_spread_pct:
            return [
                f"WIDE_SPREAD: {request.symbol} spread {spread_pct:.2%} > limit {self.max_spread_pct:.2%}"
            ]
        return []
