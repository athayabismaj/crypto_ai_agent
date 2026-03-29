"""
Lapis 7: RiskManager (The Orchestrator)
Satu-satunya gatekeeper sebelum order dieksekusi. Tidak ada bypass.
"""

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.enums import CircuitState  # type: ignore
from runtime.agent.models.risk import RiskResult, RiskVerdict, TradeRequest  # type: ignore
from runtime.agent.risk_layer.circuit_breaker import CircuitBreaker
from runtime.agent.risk_layer.exposure_control import ExposureControl
from runtime.agent.risk_layer.leverage_control import LeverageControl
from runtime.agent.risk_layer.position_size import PositionSizer
from runtime.agent.risk_layer.pre_trade_check import PreTradeCheck
from runtime.agent.risk_layer.stoploss import StopLossManager


class RiskManager:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.pre_trade = PreTradeCheck(config)
        self.circuit_breaker = CircuitBreaker(config)
        self.leverage_control = LeverageControl(config)
        self.exposure_control = ExposureControl(config)
        self.position_sizer = PositionSizer(config)
        self.stoploss = StopLossManager(config)

    def evaluate(self, request: TradeRequest, portfolio_state: dict) -> RiskResult:
        """
        Fast-fail evaluation di setiap tick.
        Pure computation (no I/O), butuh waktu < 30ms.
        """
        warnings: list[str] = []
        is_paper = getattr(self.config, "paper_mode", False)

        # Lapis 1. PreTrade
        ok, msg = self.pre_trade.validate(request, portfolio_state)
        if not ok:
            return self._block(f"PreTrade: {msg}")
        spread_warn = self.pre_trade.check_spread(request, portfolio_state)
        warnings.extend(spread_warn)

        # Lapis 2. Circuit Breaker
        # CB update logic dipanggil dari luar (tick engine) sebelum evaluate.
        # Di sini kita cukup baca statenya.
        if self.circuit_breaker.state == CircuitState.HALTED:
            msg = "CircuitBreaker DANGER: Trading is Halted"
            if is_paper:
                warnings.append(msg)
            else:
                return self._block(msg)
        elif self.circuit_breaker.state == CircuitState.WARNED:
            warnings.append("CircuitBreaker WARNING: Mendekati limit drawdown/loss harian")

        # Lapis 3. Leverage (Hanya untuk Futures)
        if request.is_futures:
            ok, msg = self.leverage_control.validate(request.leverage, request.symbol)
            if not ok:
                return self._block(f"Leverage: {msg}")

        # Lapis 4. Exposure Control
        ok, msg = self.exposure_control.validate(request, portfolio_state)
        if not ok:
            return self._block(f"Exposure: {msg}")

        # Lapis 5. Perhitungan Ukuran Lot
        equity = portfolio_state.get("equity", 0.0)
        if equity <= 0:
            return self._block("Sizing: Equity tidak valid / 0")
        qty, qty_warn, risk_usd = self.position_sizer.calculate(request, equity, portfolio_state)
        warnings.extend(qty_warn)
        if qty <= 0:
            return self._block("Sizing: Kalkulasi kuantitas final menghasilkan nol")

        # Lapis 6. Validasi dan komputasi StopLoss
        ok, msg, final_sl = self.stoploss.validate_and_compute(request, portfolio_state)
        if not ok:
            return self._block(f"StopLoss: {msg}")
        # Kalau OK tapi ada warning (seperti jarak terlalu mepet atau manual)
        if ok and msg:
            warnings.append(msg)

        final_tp = request.suggested_tp

        # Verdict Akhir
        verdict = RiskVerdict.WARNED if warnings else RiskVerdict.APPROVED

        return RiskResult(
            verdict=verdict,
            approved_quantity=qty,
            risk_amount_usd=risk_usd,
            sl_price=final_sl,
            tp_price=final_tp,
            reasons=[],
            warnings=warnings,
            sizing_method=self.position_sizer.last_method,
        )

    def _block(self, reason: str) -> RiskResult:
        """Helper to create BLOCKED RiskResult."""
        return RiskResult(verdict=RiskVerdict.BLOCKED, reasons=[reason])
