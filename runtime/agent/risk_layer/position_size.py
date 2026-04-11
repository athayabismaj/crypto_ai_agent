"""
Lapis 5: PositionSizer — Kalkulator Ukuran Posisi
====================================================
Kalkulasi ukuran posisi yang tepat berdasarkan USD at risk,
bukan notional. Mematuhi LotFilter Binance lewat math.floor.

Keputusan Arsitektur (CONTEXT.md §4.2):
    Risk budget per-trade: Hitung USD at risk = (entry−SL) × qty,
    BUKAN notional. Ini perbedaan kunci antara ritel dan institusi.

Integrasi:
    - CapitalManager.get_compound_equity() → basis equity untuk sizing
    - RiskBudgetManager.can_take_risk() → validasi budget (dipanggil dari RiskManager)
    - Lot filter Binance → math.floor ke step_size (bukan round)

Tiga Metode Sizing:
    1. fixed_fractional: Risiko X% equity per trade (default, paling aman)
    2. kelly: Optimal berdasarkan win rate & RR (butuh 50+ trade histori)
    3. volatility_scaled: Adaptif berdasarkan ATR (kecilkan saat volatile)
"""

from __future__ import annotations

import logging
import math

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore

logger = logging.getLogger(__name__)

# ── Binance Lot Defaults ─────────────────────────────────────────────────────
# Step size untuk membulatkan quantity ke kelipatan yang valid.
# math.floor digunakan (bukan round) agar tidak melebihi batas.

BINANCE_LOT_DEFAULTS: dict[str, dict[str, float]] = {
    "spot": {
        "min_qty": 0.00001,
        "step_size": 0.00001,
        "min_notional": 10.0,
    },
    "futures": {
        "min_qty": 0.001,
        "step_size": 0.001,
        "min_notional": 5.0,
        "max_notional": 1_000_000.0,
    },
}


class PositionSizer:
    """
    Mesin kalkulasi ukuran posisi.

    Output: (final_qty, warnings, risk_usd)
    - final_qty sudah di-floor ke lot filter
    - risk_usd = USD at risk = stop_distance × qty
    """

    def __init__(self, config: AgentConfig) -> None:
        self.sizing_method: str = getattr(config, "sizing_method", "fixed_fractional")
        self.risk_per_trade: float = getattr(config, "risk_per_trade_pct", 0.01)
        self.max_position_pct: float = getattr(config, "max_position_pct", 0.10)
        self.kelly_fraction: float = getattr(config, "kelly_fraction", 0.5)
        self.atr_multiplier: float = getattr(config, "atr_multiplier", 2.0)
        self.default_sl_pct: float = getattr(config, "default_sl_pct", 0.02)

        # Track last method used (untuk audit trail)
        self.last_method: str = "none"

    def calculate(
        self,
        request: TradeRequest,
        equity: float,
        portfolio_state: dict,
    ) -> tuple[float, list[str], float]:
        """
        Kalkulasi ukuran posisi final.

        Args:
            request: TradeRequest dari signal
            equity: Compound equity dari CapitalManager.get_compound_equity()
                    (BUKAN raw balance, tapi equity yang memperhitungkan paper compounding)
            portfolio_state: Dict berisi ATR, strategy stats, dll

        Returns:
            (final_qty, warnings, risk_usd)
        """
        warnings: list[str] = []
        price = request.price or portfolio_state.get(f"last_price_{request.symbol}", 0.0)

        if price <= 0:
            return 0.0, ["Price tidak valid (<= 0)"], 0.0

        if equity <= 0:
            return 0.0, ["Equity tidak valid (<= 0)"], 0.0

        method = self.sizing_method
        self.last_method = method

        try:
            if method == "kelly":
                raw_qty, w, risk_usd = self._kelly(request, equity, price, portfolio_state)
            elif method == "volatility_scaled":
                raw_qty, w, risk_usd = self._volatility_scaled(
                    request, equity, price, portfolio_state
                )
            else:
                raw_qty, w, risk_usd = self._fixed_fractional(request, equity, price)
            warnings.extend(w)
        except Exception as e:
            warnings.append(f"Metode sizing gagal {method}: {e!s}")
            raw_qty, w, risk_usd = self._fixed_fractional(request, equity, price)
            warnings.extend(w)
            self.last_method = "fixed_fractional_fallback"

        # Cek Max Limits — posisi tidak boleh melebihi max_position_pct dari equity
        max_qty_cap = (equity * self.max_position_pct) / price
        if raw_qty > max_qty_cap:
            raw_qty = max_qty_cap
            warnings.append(
                f"Position dikurangi untuk mematuhi max_position_pct ({self.max_position_pct:.1%})"
            )

        # Lot Filter — floor ke step_size (bukan round!)
        final_qty = self._apply_lot_filter(raw_qty, request.is_futures)

        # Cek Notional minimum
        notional = final_qty * price
        market_type = "futures" if request.is_futures else "spot"
        min_not = BINANCE_LOT_DEFAULTS[market_type]["min_notional"]

        if notional < min_not:
            return (
                0.0,
                warnings + [f"PositionNotional {notional:.2f} < MinNotional {min_not}"],
                risk_usd,
            )

        # Recalculate actual risk_usd berdasarkan final_qty
        stop_dist = (
            abs(price - request.suggested_sl)
            if request.suggested_sl > 0
            else price * self.default_sl_pct
        )
        actual_risk_usd = final_qty * stop_dist

        logger.debug(
            f"[PositionSizer] {request.symbol}: method={self.last_method}, "
            f"qty={final_qty}, risk_usd={actual_risk_usd:.2f}, "
            f"notional={notional:.2f}"
        )

        return final_qty, warnings, actual_risk_usd

    def _fixed_fractional(
        self, request: TradeRequest, equity: float, price: float
    ) -> tuple[float, list[str], float]:
        """
        Risiko X% equity per trade.

        Formula:
            risk_amount = equity × risk_per_trade_pct
            stop_distance = |entry - SL|
            qty = risk_amount / stop_distance
            risk_usd = min(qty × stop_distance, risk_amount)
        """
        risk_amount = equity * self.risk_per_trade
        stop_distance = abs(price - request.suggested_sl)

        if stop_distance <= 0:
            stop_distance = price * self.default_sl_pct

        quantity = risk_amount / stop_distance
        risk_usd = min(quantity * stop_distance, risk_amount)
        return quantity, [], risk_usd

    def _kelly(
        self,
        request: TradeRequest,
        equity: float,
        price: float,
        portfolio_state: dict,
    ) -> tuple[float, list[str], float]:
        """
        Kelly Criterion: f* = (b×p - q) / b

        Half-Kelly digunakan untuk keamanan (× kelly_fraction = 0.5).
        Kelly penuh sering terlalu agresif untuk trading.
        """
        warnings: list[str] = []
        stats = portfolio_state.get("strategy_stats", {}).get(request.strategy_id, {})

        if not stats:
            warnings.append(
                f"Tidak ada stats untuk {request.strategy_id}, pakai default (Win:50% RR:1.5)"
            )
            win_rate = 0.50
            avg_rr = 1.50
        else:
            win_rate = stats.get("win_rate", 0.50)
            avg_rr = stats.get("avg_risk_reward", 1.50)

        if win_rate <= 0 or win_rate >= 1:
            win_rate = 0.50
            warnings.append("win_rate tidak valid, reset ke 0.50")

        kelly_pct = max(0.0, (avg_rr * win_rate - (1.0 - win_rate)) / avg_rr) * self.kelly_fraction

        max_kelly = self.risk_per_trade * 3
        kelly_pct = min(kelly_pct, max_kelly)

        risk_amount = equity * kelly_pct
        stop_dist = abs(price - request.suggested_sl) or price * self.default_sl_pct
        quantity = risk_amount / stop_dist

        return quantity, warnings, risk_amount

    def _volatility_scaled(
        self,
        request: TradeRequest,
        equity: float,
        price: float,
        portfolio_state: dict,
    ) -> tuple[float, list[str], float]:
        """
        Sizing adaptif berdasarkan ATR.
        Saat volatilitas tinggi → posisi lebih kecil (SL lebih lebar).
        Saat volatilitas rendah → posisi lebih besar (SL lebih ketat).
        """
        warnings: list[str] = []
        atr_key = f"atr_{request.symbol}"
        if atr_key not in portfolio_state:
            warnings.append(f"ATR tidak ditemukan untuk {request.symbol}, estimasi 1.5% harga")
            atr = price * 0.015
        else:
            atr = portfolio_state[atr_key]

        stop_distance = atr * self.atr_multiplier
        risk_amount = equity * self.risk_per_trade
        quantity = risk_amount / stop_distance

        return quantity, warnings, risk_amount

    def _apply_lot_filter(self, qty: float, is_futures: bool) -> float:
        """
        Floor ke step size.

        PENTING: Gunakan math.floor, BUKAN round.
        Round bisa menghasilkan qty yang melebihi budget.
        """
        market = "futures" if is_futures else "spot"
        step = BINANCE_LOT_DEFAULTS[market]["step_size"]
        precision = max(0, round(-math.log10(step)))
        val = round(qty / step, 8)
        floored = math.floor(val) * step
        return round(floored, precision)
