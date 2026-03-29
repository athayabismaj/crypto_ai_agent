"""
Lapis 6: StopLossManager
Validasi keberadaan StopLoss dan perhitungannya (jika tak tersedia dari signal).
"""
from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore


class StopLossManager:
    def __init__(self, config: AgentConfig):
        self.require_sl = getattr(config, "require_sl", True)
        self.default_sl_pct = getattr(config, "default_sl_pct", 0.02)
        self.min_sl_distance_pct = getattr(config, "min_sl_distance_pct", 0.003)
        self.max_sl_distance_pct = getattr(config, "max_sl_distance_pct", 0.15)
        self.sl_atr_mult = getattr(config, "sl_atr_mult", 1.5)
        self.max_sl_atr_mult = getattr(config, "max_sl_atr_mult", 4.0)

    def validate_and_compute(
        self, request: TradeRequest, portfolio_state: dict
    ) -> tuple[bool, str, float]:
        """Return (ok, message, final_sl_price)"""
        price = request.price or portfolio_state.get(f"last_price_{request.symbol}", 0.0)

        if price <= 0:
            return False, "Harga tidak valid (<= 0)", 0.0

        sl = request.suggested_sl
        is_paper = getattr(portfolio_state.get("config", {}), "paper_mode", False)

        atr_key = f"atr_{request.symbol}"
        atr = portfolio_state.get(atr_key, 0.0)

        if sl <= 0:
            if self.require_sl:
                sl = self.compute_default_sl(price, request.side, atr)
                if not is_paper:
                    return False, "REQUIRE_SL: Sinyal tidak memberikan StopLoss", sl
                else:
                    return True, "WARNED_SL: Menggunakan kalkulasi default di mode simulasi", sl
            else:
                sl = self.compute_default_sl(price, request.side, atr)

        # Validasi Arah (Side)
        if request.side == "BUY" and sl >= price:
            return False, "SL di sisi yang salah untuk BUY (SL >= Entry)", sl
        if request.side == "SELL" and sl <= price:
            return False, "SL di sisi yang salah untuk SELL (SL <= Entry)", sl

        # Validasi Jarak Minimum/Maksimum
        distance = abs(price - sl) / price
        if distance < self.min_sl_distance_pct:
            return (
                False,
                f"SL terlalu dekat ({distance:.2%} < min {self.min_sl_distance_pct:.2%})",
                sl,
            )

        if distance > self.max_sl_distance_pct:
            return (
                True,
                f"SL terlalu jauh dari price (Risk amat besar = {distance:.2%})",
                sl,
            )  # Ini menjadi Warning saja nanti

        tight_ok, tight_msg = self.is_sl_tight_enough(price, sl, atr)
        if not tight_ok:
            return True, tight_msg, sl  # Warning juga

        return True, "", sl

    def compute_default_sl(self, price: float, side: str, atr: float = 0.0) -> float:
        if atr > 0:
            distance = atr * self.sl_atr_mult
        else:
            distance = price * self.default_sl_pct

        return price - distance if side == "BUY" else price + distance

    def is_sl_tight_enough(self, price: float, sl: float, atr: float) -> tuple[bool, str]:
        if atr <= 0:
            return True, ""

        distance = abs(price - sl)
        if distance > atr * self.max_sl_atr_mult:
            return (
                False,
                f"SL distance melebihi ATR maks ({distance:.2f} > {atr * self.max_sl_atr_mult:.2f})",
            )

        return True, ""
