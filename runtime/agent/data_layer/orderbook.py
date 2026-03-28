"""
OrderbookManager — Depth management dan analisis.

Menyimpan orderbook snapshot di memory dan menyediakan
query analitik: imbalance, fill price estimation, depth volume.
"""

import logging
from datetime import UTC, datetime

from runtime.agent.models import Orderbook, OrderbookLevel  # type: ignore

logger = logging.getLogger(__name__)


class OrderbookManager:
    """
    In-memory orderbook store per symbol.

    Data di-update via `update()` (dari WebSocket diff)
    atau `set_snapshot()` (dari REST).
    """

    def __init__(self) -> None:
        # {symbol: Orderbook}
        self._books: dict[str, Orderbook] = {}

    # ── Write methods ──────────────────────────────────────────

    def set_snapshot(self, ob: Orderbook) -> None:
        """Set full snapshot (biasanya dari REST initial load)."""
        self._books[ob.symbol] = ob
        logger.debug(
            "Orderbook snapshot set: %s (%d bids, %d asks)",
            ob.symbol,
            len(ob.bids),
            len(ob.asks),
        )

    def update(
        self,
        symbol: str,
        bid_updates: list[tuple[float, float]],
        ask_updates: list[tuple[float, float]],
        last_update_id: int = 0,
    ) -> None:
        """
        Apply incremental update (dari WebSocket diff depth).

        Format update: list[(price, quantity)].
        quantity=0 → hapus level.
        quantity>0 → update/tambah level.
        """
        existing = self._books.get(symbol)
        if existing is None:
            logger.warning("No snapshot for %s, skipping update", symbol)
            return

        # Cek sequence
        if (
            last_update_id > 0
            and existing.last_update_id > 0
            and last_update_id <= existing.last_update_id
        ):
            return  # stale update, skip

        # Apply bid updates
        bids = {lvl.price: lvl.quantity for lvl in existing.bids}
        for price, qty in bid_updates:
            if qty == 0:
                bids.pop(price, None)
            else:
                bids[price] = qty

        # Apply ask updates
        asks = {lvl.price: lvl.quantity for lvl in existing.asks}
        for price, qty in ask_updates:
            if qty == 0:
                asks.pop(price, None)
            else:
                asks[price] = qty

        # Rebuild sorted lists
        sorted_bids = [
            OrderbookLevel(price=p, quantity=q) for p, q in sorted(bids.items(), reverse=True)
        ]
        sorted_asks = [OrderbookLevel(price=p, quantity=q) for p, q in sorted(asks.items())]

        self._books[symbol] = Orderbook(
            symbol=symbol,
            bids=sorted_bids,
            asks=sorted_asks,
            timestamp=datetime.now(UTC),
            last_update_id=last_update_id or existing.last_update_id,
        )

    # ── Read methods ───────────────────────────────────────────

    def get_snapshot(self, symbol: str) -> Orderbook | None:
        """Return current orderbook atau None jika belum ada."""
        return self._books.get(symbol)

    def is_fresh(self, symbol: str, max_age_s: float = 5.0) -> bool:
        """Cek apakah orderbook masih fresh."""
        ob = self._books.get(symbol)
        if ob is None:
            return False
        age = (datetime.now(UTC) - ob.timestamp).total_seconds()
        return age <= max_age_s

    def get_imbalance(self, symbol: str, depth: int = 5) -> float:
        """
        Bid vs ask volume imbalance di N level teratas.

        Return -1.0 (all ask) to +1.0 (all bid).
        Return 0.0 jika tidak ada data.
        """
        ob = self._books.get(symbol)
        if ob is None:
            return 0.0

        bid_vol = sum(lvl.quantity for lvl in ob.bids[:depth])
        ask_vol = sum(lvl.quantity for lvl in ob.asks[:depth])

        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    def estimate_fill_price(self, symbol: str, qty: float, side: str) -> float:
        """
        Walk through orderbook untuk estimasi average fill price.

        side='BUY'  → walk asks (kita beli dari ask)
        side='SELL' → walk bids (kita jual ke bid)
        Return 0.0 jika data tidak cukup.
        """
        ob = self._books.get(symbol)
        if ob is None:
            return 0.0

        levels = ob.asks if side == "BUY" else ob.bids
        remaining = qty
        total_cost = 0.0

        for lvl in levels:
            fill = min(remaining, lvl.quantity)
            total_cost += fill * lvl.price
            remaining -= fill
            if remaining <= 0:
                break

        filled = qty - remaining
        if filled <= 0:
            return 0.0
        return total_cost / filled

    def get_bid_depth(self, symbol: str, pct: float = 0.01) -> float:
        """Total volume bid dalam range pct% dari best bid."""
        ob = self._books.get(symbol)
        if ob is None or not ob.bids:
            return 0.0

        threshold = ob.best_bid * (1 - pct)  # type: ignore[union-attr]
        return sum(lvl.quantity for lvl in ob.bids if lvl.price >= threshold)  # type: ignore[union-attr]

    def get_ask_depth(self, symbol: str, pct: float = 0.01) -> float:
        """Total volume ask dalam range pct% dari best ask."""
        ob = self._books.get(symbol)
        if ob is None or not ob.asks:
            return 0.0

        threshold = ob.best_ask * (1 + pct)  # type: ignore[union-attr]
        return sum(lvl.quantity for lvl in ob.asks if lvl.price <= threshold)  # type: ignore[union-attr]

    def get_symbols(self) -> list[str]:
        """Return list semua symbol yang ada di store."""
        return list(self._books.keys())
