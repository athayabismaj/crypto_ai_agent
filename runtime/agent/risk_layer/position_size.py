"""
Lapis 5: PositionSizer
Kalkulasi ukuran posisi yang tepat, mematuhi LotFilter Binance lewat operasi math.floor.
"""
import math

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore

BINANCE_LOT_DEFAULTS = {
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
    def __init__(self, config: AgentConfig):
        self.sizing_method = getattr(config, "sizing_method", "fixed_fractional")
        self.risk_per_trade = getattr(config, "risk_per_trade_pct", 0.01)
        self.max_position_pct = getattr(config, "max_position_pct", 0.10)
        self.kelly_fraction = getattr(config, "kelly_fraction", 0.5)
        self.atr_multiplier = getattr(config, "atr_multiplier", 2.0)
        self.default_sl_pct = getattr(config, "default_sl_pct", 0.02)

        # Track last method used
        self.last_method = "none"

    def calculate(
        self, request: TradeRequest, equity: float, portfolio_state: dict
    ) -> tuple[float, list[str], float]:
        warnings: list[str] = []
        price = request.price or portfolio_state.get(f"last_price_{request.symbol}", 0.0)

        if price <= 0:
            return 0.0, ["Price tidak valid (<= 0)"], 0.0

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
            warnings.append(f"Metode sizing gagal {method}: {str(e)}")
            raw_qty, w, risk_usd = self._fixed_fractional(request, equity, price)
            warnings.extend(w)
            self.last_method = "fixed_fractional_fallback"

        # Cek Max Limits
        max_qty_cap = (equity * self.max_position_pct) / price
        if raw_qty > max_qty_cap:
            raw_qty = max_qty_cap
            warnings.append(
                f"Position dikurangi untuk mematuhi max_position_pct ({self.max_position_pct:.1%})"
            )

        # Lot Filter
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

        return final_qty, warnings, risk_usd

    def _fixed_fractional(
        self, request: TradeRequest, equity: float, price: float
    ) -> tuple[float, list[str], float]:
        risk_amount = equity * self.risk_per_trade
        stop_distance = abs(price - request.suggested_sl)

        if stop_distance <= 0:
            stop_distance = price * self.default_sl_pct

        quantity = risk_amount / stop_distance
        risk_usd = min(quantity * stop_distance, risk_amount)
        return quantity, [], risk_usd

    def _kelly(
        self, request: TradeRequest, equity: float, price: float, portfolio_state: dict
    ) -> tuple[float, list[str], float]:
        warnings = []
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
        self, request: TradeRequest, equity: float, price: float, portfolio_state: dict
    ) -> tuple[float, list[str], float]:
        warnings = []
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
        """Floor ke step size."""
        market = "futures" if is_futures else "spot"
        step = BINANCE_LOT_DEFAULTS[market]["step_size"]
        precision = max(0, round(-math.log10(step)))
        val = round(qty / step, 8)
        floored = math.floor(val) * step
        return round(floored, precision)
