"""
Strategi Futures Trading (Long/Short, Leverage, Hedge).
"""

from runtime.agent.models import MarketState, Position  # type: ignore
from runtime.agent.strategy_layer.base_strategy import Signal, utcnow
from runtime.agent.strategy_layer.spot_strategy import SpotStrategy
from runtime.agent.strategy_layer.strategy_utils import tf_to_seconds


class FuturesStrategy(SpotStrategy):
    """
    Ekstensi SpotStrategy yang memungkinkan Short selling, Margin leverage
    dan mempertimbangakan Funding Rate cost.
    """

    def generate_signal(self, state: MarketState) -> Signal | None:
        # Override perilaku ALLOW_SPOT_SHORT secara aman karena kita futures
        # Kita ijinkan SELL dengan bebas
        allow_short = getattr(self.config, "allow_futures_short", True)

        # --- Pre-filter
        allowed, reason = self.is_allowed_to_trade(state)
        if not allowed:
            return None

        # --- Model prediction
        features_array = state.features.values.reshape(1, -1)
        pred = self.model.predict(features_array)[0]

        # --- Entry threshold
        entry_threshold = self._get_dynamic_threshold(state)
        if abs(pred) < entry_threshold:
            return None

        side = "BUY" if pred > 0 else "SELL"
        if side == "SELL" and not allow_short:
            return None

        if state.symbol in getattr(state, "open_positions", {}):
            return None

        # --- Futures Specific: Check Funding Rate Acceptability (Jika model MarketState memilikinya)
        funding_rate = getattr(state, "funding_rate", 0.0)
        if hasattr(self.config, "max_funding_cost_ratio") and not self._is_funding_cost_acceptable(
            side, funding_rate, pred, state.timeframe
        ):
            return None

        # --- Confidence & Build
        confidence = min(abs(pred) / (entry_threshold * 2), 1.0)
        final_conf = min(confidence * self.get_confidence_multiplier(state), 1.0)

        if final_conf < getattr(self.config, "min_signal_confidence", 0.60):
            return None

        sl_price = self._calc_sl(state.last_price, side, state.atr)
        tp_price = self._calc_tp(state.last_price, sl_price, side)

        self._last_signal[state.symbol] = utcnow()

        # Tambahkan metadata spesifik-Futures
        meta = {
            "leverage": getattr(self.config, "default_leverage", 3),
            "margin_type": getattr(self.config, "margin_type", "isolated"),
            "funding_rate_at_entry": funding_rate,
        }

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
            reasoning=f"Futures Entry: {side} (Lev: {meta['leverage']}x) via Pred={pred:.4f}",
            model_output=pred,
            regime=state.regime.value if state.regime else "",
            vol_regime=state.vol_regime,
            metadata=meta,
        )

    def generate_hedge_signal(self, spot_position: Position, state: MarketState) -> Signal | None:
        """
        Buka posisi SHORT futures untuk hedge posisi LONG spot ketika pasar diramalkan crash.
        """
        if not getattr(self.config, "hedge_enabled", False):
            return None

        features_array = state.features.values.reshape(1, -1)
        pred = self.model.predict(features_array)[0]

        hedge_threshold = getattr(self.config, "hedge_threshold", 0.005)
        # Hedge trigger hanya saat prediksi return menurun
        if pred > -hedge_threshold:
            return None

        sl_price = self._calc_sl(state.last_price, "SELL", state.atr)

        hedge_ratio = getattr(self.config, "hedge_ratio", 0.5)

        return Signal(
            symbol=state.symbol,
            side="SELL",
            strategy_id=f"{self.strategy_id}_hedge",
            timestamp=utcnow(),
            signal_type="market",
            suggested_sl=sl_price,
            confidence=min(abs(pred) / hedge_threshold, 1.0),
            reasoning=f"Hedge spot long: pred={pred:.4f}",
            metadata={"is_hedge": True, "hedge_ratio": hedge_ratio},
        )

    def _is_funding_cost_acceptable(
        self, side: str, funding_rate: float, pred_return: float, tf: str
    ) -> bool:
        """Pastikan biaya margin (funding cost) lebih rendah dibanding imbal hasil hasil prediksi."""
        hold_estimate = getattr(self.config, "avg_hold_candles", 24)
        tf_seconds = tf_to_seconds(tf)
        tf_per_8h = (8 * 3600) / tf_seconds
        if tf_per_8h <= 0:
            tf_per_8h = 1

        funding_cost = abs(funding_rate) * (hold_estimate / tf_per_8h)

        # Apakah kita yang dibebani biaya?
        paying_funding = (side == "BUY" and funding_rate > 0) or (
            side == "SELL" and funding_rate < 0
        )
        if paying_funding:
            max_acc = abs(pred_return) * getattr(self.config, "max_funding_cost_ratio", 0.3)
            return funding_cost <= max_acc

        return True  # Kalau tak bayar/dapat rebate ya malah bagus
