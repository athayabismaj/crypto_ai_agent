"""
Strategi Spot Trading (Long Only).
"""

from runtime.agent.models import MarketState, Position  # type: ignore
from runtime.agent.strategy_layer.base_strategy import BaseStrategy, ExitSignal, Signal, utcnow
from runtime.agent.strategy_layer.strategy_utils import round_price


class SpotStrategy(BaseStrategy):
    """
    Eksekusi market di ranah Spot (Tanpa leverage, tanpa kemampuan Short-Selling).
    """

    def generate_signal(self, state: MarketState) -> Signal | None:
        # ── Step 1: Pre-filter globales
        allowed, reason = self.is_allowed_to_trade(state)
        if not allowed:
            return None

        # ── Step 2: Model prediction
        features_array = state.features.values.reshape(1, -1)
        pred = self.model.predict(features_array)[0]

        # ── Step 3: Entry threshold
        entry_threshold = self._get_dynamic_threshold(state)
        if abs(pred) < entry_threshold:
            return None  # prediksi return terlalu kecil vs biaya

        side = "BUY" if pred > 0 else "SELL"

        # ── Step 4: Spot-specific filter
        if side == "SELL" and not getattr(self.config, "allow_spot_short", False):
            # Di spot kita tak bisa melakuakn open Short
            return None

        if state.symbol in getattr(state, "open_positions", {}):
            # Posisi sudah ada, no stacking by default
            return None

        # ── Step 5: Final Confidence calculation
        # Contoh kalkulasi confidence, default menggunakan ratio pred/threshold cap 1.0
        confidence = min(abs(pred) / (entry_threshold * 2), 1.0)
        multiplier = self.get_confidence_multiplier(state)
        final_conf = min(confidence * multiplier, 1.0)

        if final_conf < getattr(self.config, "min_signal_confidence", 0.60):
            return None

        # ── Step 6: Kalkulasi SL / TP
        sl_price = self._calc_sl(state.last_price, side, state.atr)
        tp_price = self._calc_tp(state.last_price, sl_price, side)

        # ── Step 7: Update cooldown dan lepaskan signal
        self._last_signal[state.symbol] = utcnow()

        return Signal(
            symbol=state.symbol,
            side=side,
            strategy_id=self.strategy_id,
            timestamp=utcnow(),
            signal_type="market",
            suggested_price=0.0,
            suggested_sl=sl_price,
            suggested_tp=tp_price,
            confidence=confidence,
            final_confidence=final_conf,
            reasoning=f"Spot Entry: {side} via Pred={pred:.4f}",
            model_output=pred,
            regime=state.regime.value if state.regime else "",
            vol_regime=state.vol_regime,
        )

    def should_exit(self, position: Position, state: MarketState) -> ExitSignal | None:
        """Kapan posisi keluar sebelum SL/TP disenggol."""
        from runtime.agent.models import MarketRegime  # type: ignore

        # --- 1: Evaluasi Flip Regime
        if position.side == "BUY":
            adverse_regimes = [MarketRegime.STRONG_TREND_DOWN, MarketRegime.WEAK_TREND_DOWN]
        else:
            adverse_regimes = [MarketRegime.STRONG_TREND_UP, MarketRegime.WEAK_TREND_UP]

        if state.regime in adverse_regimes:
            return ExitSignal(
                position_id=getattr(position, "trade_id", "unknown"),
                reason="regime_change",
                urgency="normal",
                exit_price=0.0,
                confidence=0.9,
                metadata={"regime": state.regime.value},
            )

        # --- 2: Evaluasi Model Flip (Berbalik arah secara teknikal ML)
        pred = self.model.predict(state.features.values.reshape(1, -1))[0]
        flip_threshold = getattr(self.config, "model_flip_threshold", 0.005)

        if position.side == "BUY" and pred < -flip_threshold:
            return ExitSignal(
                position_id=getattr(position, "trade_id", "unknown"),
                reason="model_flip",
                urgency="normal",
                exit_price=0.0,
                confidence=min(abs(pred) / flip_threshold, 1.0),
            )

        return None

    def get_signal_metadata(self, state: MarketState) -> dict:  # type: ignore
        return {"strategy_class": self.strategy_id, "vol_regime": state.vol_regime}

    # ── Internal Helpers ──────────────────────────────────────────────

    def _get_dynamic_threshold(self, state: MarketState) -> float:
        """
        Threshold semakin melar jika volatility sedang 'high' atau 'extreme'.
        Juga ditambah dengan spread percentage rate.
        """
        base_threshold = getattr(self.config, "model_threshold", 0.003)
        vol_multiplier = {"low": 0.8, "normal": 1.0, "high": 1.3, "extreme": 2.0}.get(
            state.vol_regime, 1.0
        )
        spread_cost = state.spread_pct / 100.0
        return (base_threshold * vol_multiplier) + spread_cost

    def _calc_sl(self, price: float, side: str, atr: float, tick_size: float = 0.01) -> float:
        """Kalkulasi SL."""
        distance = atr * getattr(self.config, "sl_atr_multiplier", 1.5)
        # Terapkan Floor (Setidaknya 2% default_sl_pct)
        default_floor = price * getattr(self.config, "default_sl_pct", 0.02)
        distance = max(distance, default_floor)

        if side == "BUY":
            return round_price(price - distance, tick_size)
        else:
            return round_price(price + distance, tick_size)

    def _calc_tp(self, price: float, sl: float, side: str, tick_size: float = 0.01) -> float:
        """Kalkulasi Take-Profit berdasarkan RR rasio terhadap jarak SL."""
        risk = abs(price - sl)
        reward = risk * getattr(self.config, "default_tp_ratio", 2.0)

        if side == "BUY":
            return round_price(price + reward, tick_size)
        else:
            return round_price(price - reward, tick_size)
