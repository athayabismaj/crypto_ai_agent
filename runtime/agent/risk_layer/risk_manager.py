"""
Lapis 7: RiskManager — Orkestrator Utama Risk Layer
======================================================
Satu-satunya gatekeeper sebelum order dieksekusi. Tidak ada bypass.

Filosofi (Market Analyst View, 20+ tahun):
    Ini adalah orang tua yang mengunci pintu rumah sebelum anak
    keluar malam. Tidak peduli anak bilang "aman kok" — pintu
    tetap dikunci sampai semua kondisi terpenuhi.

Perubahan Arsitektur Kunci (dari skeleton):
    Skeleton lama: 6 lapis evaluasi, tidak ada Portfolio integration.
    Versi baru: 10 lapis evaluasi, terintegrasi penuh dengan:
    - CapitalManager (Gate L1: safe_to_trade + compound equity)
    - CorrelationController (Gate L6: korelasi antar-aset)
    - RiskBudgetManager (Gate L8: budget per strategi)
    - PortfolioAllocator (Gate L9: kuota alokasi)

Paper Mode Behavior (CONTEXT.md + skill contract):
    Di paper mode, BLOCKED → WARNED. Qty tetap terisi.
    Paper mode TIDAK pernah benar-benar block — hanya mencatat.
    Ini agar simulasi bisa berjalan lengkap untuk evaluasi strategi.

Alur Evaluasi (10 Lapis):
    L01: CapitalManager.safe_to_trade?
    L02: PreTradeCheck.validate()
    L03: CircuitBreaker state check
    L04: LeverageControl (futures only)
    L05: ExposureControl.validate()
    L06: CorrelationController.check()
    L07: PositionSizer.calculate() — dengan compound equity
    L08: RiskBudgetManager.can_take_risk()
    L09: PortfolioAllocator.can_open()
    L10: StopLoss.validate_and_compute()
    → Rakit RiskResult + paper mode override
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.enums import CircuitState, RiskVerdict  # type: ignore
from runtime.agent.models.risk import RiskResult, TradeRequest  # type: ignore
from runtime.agent.portfolio_layer.allocator import PortfolioAllocator  # type: ignore
from runtime.agent.portfolio_layer.capital_manager import CapitalManager  # type: ignore
from runtime.agent.portfolio_layer.correlation import CorrelationController  # type: ignore
from runtime.agent.portfolio_layer.risk_budget import RiskBudgetManager  # type: ignore
from runtime.agent.risk_layer.circuit_breaker import CircuitBreaker
from runtime.agent.risk_layer.exposure_control import ExposureControl
from runtime.agent.risk_layer.leverage_control import LeverageControl
from runtime.agent.risk_layer.position_size import PositionSizer
from runtime.agent.risk_layer.pre_trade_check import PreTradeCheck
from runtime.agent.risk_layer.stoploss import StopLossManager

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskManager:
    """
    Orkestrator evaluasi risiko — satu-satunya pintu masuk ke risk evaluation.

    Semua trade request WAJIB melewati evaluate() sebelum dikirim ke exchange.
    Tidak ada jalur bypass. Tidak ada pengecualian.

    Constructor injection: Semua Portfolio Layer components di-inject saat init.
    Ini memastikan coupling yang jelas dan testable.
    """

    def __init__(
        self,
        config: AgentConfig,
        capital_manager: CapitalManager,
        correlation_controller: CorrelationController,
        risk_budget_manager: RiskBudgetManager,
        portfolio_allocator: PortfolioAllocator,
    ) -> None:
        self.config = config
        self.paper_mode: bool = getattr(config, "paper_mode", True)

        # ── Portfolio Layer (injected) ─────────────────────────────────────
        self.capital_manager = capital_manager
        self.correlation_controller = correlation_controller
        self.risk_budget_manager = risk_budget_manager
        self.portfolio_allocator = portfolio_allocator

        # ── Risk Layer (internal) ──────────────────────────────────────────
        self.pre_trade = PreTradeCheck(config)
        self.circuit_breaker = CircuitBreaker(config)
        self.leverage_control = LeverageControl(config)
        self.exposure_control = ExposureControl(config)
        self.position_sizer = PositionSizer(config)
        self.stoploss = StopLossManager(config)

        # ── Internal state ─────────────────────────────────────────────────
        self._daily_pnl: float = 0.0
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0

        logger.info(
            f"[RiskManager] Initialized. paper_mode={self.paper_mode}. "
            f"10-layer evaluation pipeline ready."
        )

    def evaluate(self, request: TradeRequest, portfolio_state: dict) -> RiskResult:
        """
        Evaluasi penuh satu trade request melalui 10 lapis validasi.

        Satu-satunya fungsi yang perlu dipanggil dari luar.
        Pure computation (no I/O), target < 30ms.

        Args:
            request: TradeRequest dari Signal
            portfolio_state: Dict berisi posisi, harga, ATR, dll

        Returns:
            RiskResult dengan verdict APPROVED/WARNED/BLOCKED
        """
        warnings: list[str] = []

        # ── L01: CapitalManager — safe_to_trade? ───────────────────────────
        capital_status = self.capital_manager.get_status()
        if not capital_status.safe_to_trade:
            reason = (
                f"CapitalManager: Trading halted. "
                f"Warnings: {'; '.join(capital_status.warnings)}"
            )
            return self._finalize(
                RiskVerdict.BLOCKED, request, reasons=[reason], warnings=warnings
            )

        # Update internal equity state dari CapitalManager
        self._current_equity = capital_status.current_equity
        self._peak_equity = capital_status.peak_equity
        self._daily_pnl = capital_status.daily_pnl

        # ── L02: PreTradeCheck — data valid? ───────────────────────────────
        ok, msg = self.pre_trade.validate(request, portfolio_state)
        if not ok:
            return self._finalize(
                RiskVerdict.BLOCKED, request, reasons=[f"PreTrade: {msg}"], warnings=warnings
            )
        spread_warn = self.pre_trade.check_spread(request, portfolio_state)
        warnings.extend(spread_warn)

        # ── L03: CircuitBreaker — trading masih boleh? ─────────────────────
        cb_state = self.circuit_breaker.check(
            self._daily_pnl,
            self._current_equity,
            self._peak_equity,
        )
        if cb_state == CircuitState.HALTED:
            return self._finalize(
                RiskVerdict.BLOCKED,
                request,
                reasons=["CircuitBreaker: Trading is HALTED"],
                warnings=warnings,
            )
        elif cb_state == CircuitState.WARNED:
            warnings.append("CircuitBreaker: Mendekati limit drawdown/loss harian")

        # ── L04: LeverageControl — leverage dalam batas? (futures only) ────
        if request.is_futures:
            ok, msg = self.leverage_control.validate(request.leverage, request.symbol)
            if not ok:
                return self._finalize(
                    RiskVerdict.BLOCKED, request, reasons=[f"Leverage: {msg}"], warnings=warnings
                )

        # ── L05: ExposureControl — portfolio tidak over-exposed? ───────────
        ok, msg = self.exposure_control.validate(request, portfolio_state)
        if not ok:
            return self._finalize(
                RiskVerdict.BLOCKED, request, reasons=[f"Exposure: {msg}"], warnings=warnings
            )

        # ── L06: CorrelationController — korelasi antar-aset aman? ─────────
        equity = self.capital_manager.get_compound_equity()
        price = request.price or portfolio_state.get(f"last_price_{request.symbol}", 0.0)
        new_notional = request.quantity * price if price > 0 else 0.0
        positions = portfolio_state.get("open_positions", {})

        ok, msg = self.correlation_controller.check(
            new_symbol=request.symbol,
            new_side=request.side,
            new_notional=new_notional,
            positions=positions,
            equity=equity,
        )
        if not ok:
            return self._finalize(
                RiskVerdict.BLOCKED, request, reasons=[f"Correlation: {msg}"], warnings=warnings
            )

        # ── L07: PositionSizer — berapa qty yang aman? ─────────────────────
        if equity <= 0:
            return self._finalize(
                RiskVerdict.BLOCKED,
                request,
                reasons=["Sizing: Equity tidak valid / 0"],
                warnings=warnings,
            )

        qty, qty_warn, risk_usd = self.position_sizer.calculate(
            request, equity, portfolio_state
        )
        warnings.extend(qty_warn)

        if qty <= 0:
            return self._finalize(
                RiskVerdict.BLOCKED,
                request,
                reasons=["Sizing: Kalkulasi kuantitas final menghasilkan nol"],
                warnings=warnings,
            )

        # ── L08: RiskBudgetManager — budget strategi masih ada? ────────────
        ok, msg = self.risk_budget_manager.can_take_risk(
            strategy_id=request.strategy_id,
            risk_amount=risk_usd,
            equity=equity,
        )
        if not ok:
            return self._finalize(
                RiskVerdict.BLOCKED, request, reasons=[f"RiskBudget: {msg}"], warnings=warnings
            )

        # ── L09: PortfolioAllocator — ada kuota alokasi? ───────────────────
        sized_notional = qty * price if price > 0 else 0.0
        ok, msg = self.portfolio_allocator.can_open(
            strategy_id=request.strategy_id,
            symbol=request.symbol,
            notional=sized_notional,
            equity=equity,
            positions=positions,
        )
        if not ok:
            return self._finalize(
                RiskVerdict.BLOCKED, request, reasons=[f"Allocator: {msg}"], warnings=warnings
            )

        # ── L10: StopLoss — SL valid dan dalam range wajar? ───────────────
        sl_ok, sl_msg, final_sl = self.stoploss.validate_and_compute(request, portfolio_state)
        if not sl_ok:
            return self._finalize(
                RiskVerdict.BLOCKED, request, reasons=[f"StopLoss: {sl_msg}"], warnings=warnings
            )
        if sl_ok and sl_msg:
            warnings.append(f"StopLoss: {sl_msg}")

        final_tp = request.suggested_tp

        # ── Verdict Akhir ──────────────────────────────────────────────────
        verdict = RiskVerdict.WARNED if warnings else RiskVerdict.APPROVED

        result = RiskResult(
            verdict=verdict,
            approved_quantity=qty,
            risk_amount_usd=risk_usd,
            sl_price=final_sl,
            tp_price=final_tp,
            reasons=[],
            warnings=warnings,
            sizing_method=self.position_sizer.last_method,
        )

        # Paper mode override: BLOCKED tidak pernah terjadi di paper
        # (sudah ditangani di _finalize, tapi double-check di sini)
        self._log_result(request, result)
        return result

    def _finalize(
        self,
        verdict: RiskVerdict,
        request: TradeRequest,
        reasons: list[str] | None = None,
        warnings: list[str] | None = None,
        qty: float = 0.0,
        risk_usd: float = 0.0,
        sl_price: float = 0.0,
        tp_price: float = 0.0,
    ) -> RiskResult:
        """
        Helper untuk merakit RiskResult dengan paper mode override.

        Di paper mode:
        - BLOCKED → WARNED
        - qty tetap terisi (request.quantity jika sizing belum jalan)
        - reasons dipindah ke warnings
        """
        _reasons = reasons or []
        _warnings = warnings or []

        result = RiskResult(
            verdict=verdict,
            approved_quantity=qty,
            risk_amount_usd=risk_usd,
            sl_price=sl_price,
            tp_price=tp_price,
            reasons=_reasons,
            warnings=_warnings,
            sizing_method=self.position_sizer.last_method,
        )

        # Paper mode: BLOCKED → WARNED, qty tetap diisi
        if self.paper_mode and result.verdict == RiskVerdict.BLOCKED:
            result.verdict = RiskVerdict.WARNED
            result.approved_quantity = qty if qty > 0 else request.quantity
            result.warnings = _reasons + _warnings
            result.reasons = []

        self._log_result(request, result)
        return result

    def _log_result(self, request: TradeRequest, result: RiskResult) -> None:
        """Log hasil evaluasi untuk audit trail."""
        if result.verdict == RiskVerdict.BLOCKED:
            logger.warning(
                f"[RiskManager] BLOCKED {request.side} {request.symbol}: "
                f"{'; '.join(result.reasons)}"
            )
        elif result.verdict == RiskVerdict.WARNED:
            logger.info(
                f"[RiskManager] WARNED {request.side} {request.symbol} "
                f"qty={result.approved_quantity:.6f}: {'; '.join(result.warnings)}"
            )
        else:
            logger.info(
                f"[RiskManager] APPROVED {request.side} {request.symbol} "
                f"qty={result.approved_quantity:.6f} risk=${result.risk_amount_usd:.2f}"
            )

    # ── Equity & Lifecycle Methods ──────────────────────────────────────────

    def update_equity(self, current_equity: float) -> None:
        """
        Dipanggil setelah setiap trade close atau sync periodik.
        Update internal state untuk circuit breaker & drawdown check.
        """
        self._current_equity = current_equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

    def reset_daily_stats(self) -> None:
        """
        Dipanggil scheduler UTC 00:00. Reset daily PnL counter.
        Delegasikan ke sub-components yang juga perlu daily reset.
        """
        self._daily_pnl = 0.0
        self.circuit_breaker.reset_daily()
        logger.info("[RiskManager] Daily stats reset complete.")

    def get_risk_summary(self) -> dict:
        """
        Snapshot risk state saat ini untuk monitoring dashboard.

        Returns:
            Dict berisi status semua sub-komponen risk.
        """
        return {
            "timestamp": _utcnow().isoformat(),
            "paper_mode": self.paper_mode,
            "current_equity": self._current_equity,
            "peak_equity": self._peak_equity,
            "daily_pnl": self._daily_pnl,
            "circuit_breaker": self.circuit_breaker.status(),
            "leverage_max": self.leverage_control.global_max,
            "sizing_method": self.position_sizer.sizing_method,
        }
