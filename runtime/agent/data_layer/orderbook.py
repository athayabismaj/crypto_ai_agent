"""
Layer Data: Buku Ordo (Orderbook)
Mengkonstruksi Kedalaman Harga (Liquidity Depth) 
dari snapshot HTTP + Delta Stream Binance.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from runtime.agent.data_layer.validator import utcnow


@dataclass
class OrderbookLevel:
    price: float
    quantity: float


@dataclass
class Orderbook:
    symbol: str
    bids: list[OrderbookLevel] = field(default_factory=list)  # DESC (Tertinggi dulu)
    asks: list[OrderbookLevel] = field(default_factory=list)  # ASC (Terendah dulu)
    timestamp: datetime = field(default_factory=utcnow)
    last_update_id: int = 0

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        return 0.0

    @property
    def spread_pct(self) -> float:
        mid = self.mid_price
        if mid == 0:
            return 0.0
        return ((self.best_ask - self.best_bid) / mid) * 100

    def get_bid_depth(self, pct: float = 0.01) -> float:
        limit = self.best_bid * (1 - pct)
        total_qty = 0.0
        for b in self.bids:
            if b.price >= limit:
                total_qty += b.quantity
            else:
                break
        return total_qty

    def get_ask_depth(self, pct: float = 0.01) -> float:
        limit = self.best_ask * (1 + pct)
        total_qty = 0.0
        for a in self.asks:
            if a.price <= limit:
                total_qty += a.quantity
            else:
                break
        return total_qty

    def estimate_fill_price(self, qty: float, side: str) -> float:
        remaining = qty
        total_cost = 0.0

        levels = self.asks if side.upper() == "BUY" else self.bids
        if not levels:
            return 0.0

        for lvl in levels:
            take = min(remaining, lvl.quantity)
            total_cost += take * lvl.price
            remaining -= take

            if remaining <= 0:
                break

        # Kalau slippage melewati batas book, kita return harga rata-rata yang bisa dieksekusi
        filled = qty - remaining
        if filled == 0:
            return 0.0

        return total_cost / filled

    def get_imbalance(self, depth: int = 5) -> float:
        b_vol = sum(b.quantity for b in self.bids[:depth])
        a_vol = sum(a.quantity for a in self.asks[:depth])

        if b_vol + a_vol == 0:
            return 0.0

        # +1.0 = full bid (bullish), -1.0 = full ask (bearish)
        return (b_vol - a_vol) / (b_vol + a_vol)


class OrderbookStore:
    """Manajer Sinkronisasi Delta Stream Orderbook dari Binance."""

    MAX_FRESH_AGE_S: ClassVar[float] = 5.0

    def __init__(self) -> None:
        self._books: dict[str, Orderbook] = {}

    def get_latest(self, symbol: str) -> Orderbook | None:
        return self._books.get(symbol)

    def is_fresh(self, symbol: str, max_age_s: float | None = None) -> bool:
        limit = max_age_s or self.MAX_FRESH_AGE_S
        ob = self.get_latest(symbol)
        if not ob:
            return False

        age = (utcnow() - ob.timestamp).total_seconds()
        return age <= limit

    def apply_snapshot(
        self,
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        up_id: int,
    ) -> None:
        # Merakit list
        b = [OrderbookLevel(price=p, quantity=q) for p, q in bids if q > 0]
        a = [OrderbookLevel(price=p, quantity=q) for p, q in asks if q > 0]

        # Sort pasti (bids DESC, asks ASC)
        b.sort(key=lambda x: x.price, reverse=True)
        a.sort(key=lambda x: x.price)

        self._books[symbol] = Orderbook(
            symbol=symbol, bids=b, asks=a, timestamp=utcnow(), last_update_id=up_id
        )

    def apply_delta(
        self,
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        up_id: int,
    ) -> None:
        """Delta Incremental Updates (wss://stream.../depth@100ms)"""
        if symbol not in self._books:
            return

        ob = self._books[symbol]

        # Jika Event Lama (out-of-order)
        if up_id <= ob.last_update_id:
            return

        # Update Bids
        b_dict = {lvl.price: lvl for lvl in ob.bids}
        for p, q in bids:
            if q == 0 and p in b_dict:
                del b_dict[p]
            elif q > 0:
                if p in b_dict:
                    b_dict[p].quantity = q
                else:
                    b_dict[p] = OrderbookLevel(p, q)

        # Update Asks
        a_dict = {lvl.price: lvl for lvl in ob.asks}
        for p, q in asks:
            if q == 0 and p in a_dict:
                del a_dict[p]
            elif q > 0:
                if p in a_dict:
                    a_dict[p].quantity = q
                else:
                    a_dict[p] = OrderbookLevel(p, q)

        # Re-construct lists + re-sort
        ob.bids = sorted(list(b_dict.values()), key=lambda x: x.price, reverse=True)
        ob.asks = sorted(list(a_dict.values()), key=lambda x: x.price)

        # Update meta
        ob.timestamp = utcnow()
        ob.last_update_id = up_id
