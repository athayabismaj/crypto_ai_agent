"""
Strategy Layer: StrategyRegistry & SignalProcessor
=======================================================
Orchestrator eksekusi multi-strategi — lem penghubung antara
Strategy Layer, Portfolio Layer, dan Risk Layer.

Filosofi Desain (Market Analyst View, 20+ tahun):
-----------
Bot trading ritel biasanya hanya punya 1 strategi yang berjalan.
Bot institusional berjalan dengan 5-20 strategi secara paralel,
masing-masing spesialis di pair/timeframe tertentu.

Masalah: 2 strategi berbeda bisa sama-sama menghasilkan sinyal pada
simbol yang sama di tick yang sama → konflik atau duplikasi order.

StrategyRegistry selesaikan ini dengan:
1. REGISTER: Daftarkan strategi + simbol yang di-handle + bobot
2. SCAN: Setiap tick, panggil generate_signal() semua strategi aktif
3. RESOLVE: Tangani konflik (BUY vs SELL di simbol sama → block keduanya)
4. PRIORITIZE: Sort by confidence DESC, batasi MAX_SIGNALS_PER_TICK
5. LIFECYCLE: enable/disable strategi tanpa ganggu posisi existing

SignalProcessor:
- Orchestrasi pemeriksaan berlapis (Portfolio → Correlation → Risk Budget)
- Return ProcessResult yang terstruktur untuk logging & observability
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums & Result Types ───────────────────────────────────────────────────


class ProcessResult(Enum):
    """Hasil pemrosesan signal oleh SignalProcessor."""

    APPROVED = "APPROVED"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"  # Market tidak aman (anomaly)
    BLOCKED_CAPITAL = "BLOCKED_CAPITAL"  # CapitalManager safe_to_trade=False
    BLOCKED_ALLOCATION = "BLOCKED_ALLOCATION"  # PortfolioAllocator limit terlampaui
    BLOCKED_CORRELATION = "BLOCKED_CORRELATION"  # CorrelationController group breach
    BLOCKED_BUDGET = "BLOCKED_BUDGET"  # RiskBudgetManager daily habis
    BLOCKED_RISK = "BLOCKED_RISK"  # RiskManager final gate
    BLOCKED_CONFLICT = "BLOCKED_CONFLICT"  # Signal BUY+SELL konflik di simbol sama
    ERROR = "ERROR"  # Exception tidak terduga


@dataclass
class StrategyEntry:
    """Metadata registrasi satu strategi."""

    strategy: object  # BaseStrategy instance
    strategy_id: str
    symbols: list[str]  # Simbol yang di-handle strategi ini
    weight: float  # Bobot untuk RiskBudget allocation
    is_active: bool = True
    disabled_reason: str = ""
    disabled_at: datetime | None = None
    registered_at: datetime = field(default_factory=_utcnow)
    total_signals_generated: int = 0
    total_signals_approved: int = 0


@dataclass
class SignalCandidate:
    """Signal + metadata prioritas untuk resolusi konflik."""

    signal: object  # Signal dataclass
    strategy_id: str
    symbol: str
    side: str
    confidence: float
    generated_at: datetime = field(default_factory=_utcnow)


@dataclass
class SignalConflict:
    """Log konflik sinyal untuk audit trail."""

    symbol: str
    signals: list[SignalCandidate]
    conflict_type: str  # 'direction_conflict' | 'duplicate'
    resolved_as: str  # 'blocked' | 'highest_confidence'
    timestamp: datetime = field(default_factory=_utcnow)


# ── StrategyRegistry ──────────────────────────────────────────────────────


class StrategyRegistry:
    """
    Manajer lifecycle dan eksekusi semua strategi aktif.

    Thread Safety:
    - Registry ini diakses oleh main_loop (single-threaded asyncio)
    - Tidak perlu lock jika seluruh main_loop adalah coroutine tunggal
    - Jika multi-threaded: tambahkan asyncio.Lock di masa depan
    """

    def __init__(self, config: object) -> None:
        self.config = config
        self._entries: dict[str, StrategyEntry] = {}

        # Batas sinyal per tick (mencegah overload di satu waktu)
        self.max_signals_per_tick: int = getattr(config, "max_signals_per_tick", 3)

        # Riwayat konflik untuk audit
        self._conflict_log: list[SignalConflict] = []

        logger.info("[StrategyRegistry] Initialized.")

    def register(
        self,
        strategy: object,
        symbols: list[str],
        weight: float = 1.0,
    ) -> None:
        """
        Daftarkan strategi baru ke registry.

        Args:
            strategy: Instance BaseStrategy
            symbols: List simbol yang di-handle (contoh: ['BTCUSDT', 'ETHUSDT'])
            weight: Bobot relatif untuk risk budget allocation (default 1.0)

        Raises:
            ValueError: Jika strategy_id sudah terdaftar
        """
        strat_id = getattr(strategy, "strategy_id", strategy.__class__.__name__)

        if strat_id in self._entries:
            logger.warning(
                f"[StrategyRegistry] Strategy '{strat_id}' already registered. "
                "Updating symbols and weight."
            )
            entry = self._entries[strat_id]
            entry.symbols = symbols
            entry.weight = weight
            return

        entry = StrategyEntry(
            strategy=strategy,
            strategy_id=strat_id,
            symbols=symbols,
            weight=weight,
        )
        self._entries[strat_id] = entry

        logger.info(
            f"[StrategyRegistry] Registered '{strat_id}' — " f"symbols={symbols}, weight={weight}"
        )

    def disable(self, strategy_id: str, reason: str = "") -> None:
        """
        Non-aktifkan strategi.

        PENTING: Posisi yang sudah terbuka TIDAK ditutup otomatis.
        Trade Manager tetap me-manage posisi existing sampai close natural.
        Hanya sinyal BARU yang dihentikan.
        """
        entry = self._entries.get(strategy_id)
        if not entry:
            logger.warning(f"[StrategyRegistry] Cannot disable unknown strategy: {strategy_id}")
            return

        entry.is_active = False
        entry.disabled_reason = reason
        entry.disabled_at = _utcnow()

        logger.warning(
            f"[StrategyRegistry] Strategy '{strategy_id}' DISABLED. "
            f"Reason: {reason or 'manual'}"
        )

    def enable(self, strategy_id: str) -> None:
        """Re-aktifkan strategi setelah review/recovery."""
        entry = self._entries.get(strategy_id)
        if not entry:
            logger.warning(f"[StrategyRegistry] Cannot enable unknown strategy: {strategy_id}")
            return

        entry.is_active = True
        entry.disabled_reason = ""
        entry.disabled_at = None

        logger.info(f"[StrategyRegistry] Strategy '{strategy_id}' RE-ENABLED.")

    def get_active_strategies(
        self,
        symbol: str | None = None,
    ) -> list[object]:
        """
        Return list BaseStrategy yang aktif.
        Jika symbol disediakan, filter hanya yang handle symbol tersebut.
        """
        result = []
        for entry in self._entries.values():
            if not entry.is_active:
                continue
            if symbol and symbol not in entry.symbols:
                continue
            result.append(entry.strategy)
        return result

    def get_signals(self, state: object) -> list[object]:
        """
        Panggil generate_signal() semua strategi aktif untuk state ini.

        Proses:
        1. Kumpulkan semua signal kandidat
        2. Deteksi dan resolve konflik
        3. Sort by confidence DESC
        4. Batasi ke MAX_SIGNALS_PER_TICK

        Returns:
            List Signal (sudah difilter dan diurutkan)
        """
        symbol: str = getattr(state, "symbol", "")
        active_strategies = self.get_active_strategies(symbol=symbol)

        if not active_strategies:
            return []

        # Kumpulkan semua kandidat
        candidates: list[SignalCandidate] = []

        for strat in active_strategies:
            strat_id = getattr(strat, "strategy_id", "unknown")
            try:
                signal = strat.generate_signal(state)  # type: ignore[attr-defined]
                if signal is None:
                    continue

                candidate = SignalCandidate(
                    signal=signal,
                    strategy_id=strat_id,
                    symbol=getattr(signal, "symbol", symbol),
                    side=getattr(signal, "side", ""),
                    confidence=getattr(signal, "final_confidence", 0.0),
                )
                candidates.append(candidate)

                # Update counter
                entry = self._entries.get(strat_id)
                if entry:
                    entry.total_signals_generated += 1

            except Exception as exc:
                logger.error(
                    f"[StrategyRegistry] Error in '{strat_id}'.generate_signal(): {exc}",
                    exc_info=True,
                )

        if not candidates:
            return []

        # Resolve konflik
        resolved = self._resolve_conflicts(candidates)

        # Sort by confidence DESC, batasi jumlah
        resolved.sort(key=lambda c: c.confidence, reverse=True)
        top_candidates = resolved[: self.max_signals_per_tick]

        return [c.signal for c in top_candidates]

    def get_status(self) -> dict:
        """Status semua strategi untuk monitoring & health check."""
        result: dict = {
            "total_registered": len(self._entries),
            "total_active": sum(1 for e in self._entries.values() if e.is_active),
            "strategies": {},
        }

        for strat_id, entry in self._entries.items():
            approval_rate = 0.0
            if entry.total_signals_generated > 0:
                approval_rate = entry.total_signals_approved / entry.total_signals_generated * 100

            result["strategies"][strat_id] = {
                "is_active": entry.is_active,
                "symbols": entry.symbols,
                "weight": entry.weight,
                "signals_generated": entry.total_signals_generated,
                "signals_approved": entry.total_signals_approved,
                "approval_rate_pct": round(approval_rate, 1),
                "disabled_reason": entry.disabled_reason,
                "registered_at": entry.registered_at.isoformat(),
            }

        result["recent_conflicts"] = len(self._conflict_log[-10:])
        return result

    def record_signal_approved(self, strategy_id: str) -> None:
        """Dipanggil oleh SignalProcessor saat signal lolos semua gate."""
        entry = self._entries.get(strategy_id)
        if entry:
            entry.total_signals_approved += 1

    # ── Private Helpers ────────────────────────────────────────────────────

    def _resolve_conflicts(self, candidates: list[SignalCandidate]) -> list[SignalCandidate]:
        """
        Strategi conflict resolution:

        Rule 1: Jika ada BUY dan SELL di simbol yang sama → BLOCK KEDUANYA
                (Konflik arah = pasar tidak jelas = jangan trade)

        Rule 2: Jika ada 2+ signal BUY di simbol yang sama → ambil confidence tertinggi

        Rule 3: Jika confidence sama persis → ambil yang lebih dulu (FIFO)
        """
        # Group by symbol
        by_symbol: dict[str, list[SignalCandidate]] = {}
        for c in candidates:
            by_symbol.setdefault(c.symbol, []).append(c)

        resolved: list[SignalCandidate] = []

        for sym, sym_candidates in by_symbol.items():
            if len(sym_candidates) == 1:
                resolved.append(sym_candidates[0])
                continue

            sides = {c.side for c in sym_candidates}

            if len(sides) > 1:
                # Rule 1: Konflik arah → Block semua
                conflict = SignalConflict(
                    symbol=sym,
                    signals=sym_candidates,
                    conflict_type="direction_conflict",
                    resolved_as="blocked",
                )
                self._conflict_log.append(conflict)
                logger.warning(
                    f"[StrategyRegistry] CONFLICT on {sym}: "
                    f"{[f'{c.strategy_id}→{c.side}({c.confidence:.2f})' for c in sym_candidates]}. "
                    "ALL BLOCKED."
                )
                continue

            # Rule 2: Semua arah sama → ambil confidence tertinggi
            best = max(sym_candidates, key=lambda c: (c.confidence, -c.generated_at.timestamp()))

            if len(sym_candidates) > 1:
                conflict = SignalConflict(
                    symbol=sym,
                    signals=sym_candidates,
                    conflict_type="duplicate",
                    resolved_as="highest_confidence",
                )
                self._conflict_log.append(conflict)
                logger.info(
                    f"[StrategyRegistry] Duplicate signals on {sym}: "
                    f"chose '{best.strategy_id}' (conf={best.confidence:.2f})."
                )

            resolved.append(best)

        return resolved


# ── SignalProcessor ───────────────────────────────────────────────────────


@dataclass
class ProcessedSignal:
    """Hasil lengkap pemrosesan satu signal melalui semua gate."""

    signal: object
    result: ProcessResult
    reason: str
    estimated_notional: float = 0.0
    risk_amount_usd: float = 0.0
    approved_at: datetime | None = None

    @property
    def is_approved(self) -> bool:
        return self.result == ProcessResult.APPROVED


class SignalProcessor:
    """
    Orchestrator validasi signal berlapis.

    Urutan gate (sesuai dokumentasi):
    1. Capital check (safe_to_trade?)
    2. Allocation check (portfolio kuota cukup?)
    3. Correlation check (tidak melebihi grup limit?)
    4. Risk budget check (masih punya anggaran risk hari ini?)

    Catatan:
    - Risk Layer (risk_manager.evaluate) ada di luar scope ini
      → Dipanggil oleh main_loop setelah SignalProcessor.process() APPROVED
    - SignalProcessor = Portfolio Gate
    - risk_manager = Final Execution Gate
    """

    def __init__(
        self,
        capital_manager: object,
        allocator: object,
        correlation: object,
        risk_budget: object,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self._capital = capital_manager
        self._allocator = allocator
        self._correlation = correlation
        self._risk_budget = risk_budget
        self._registry = registry

    def process(
        self,
        signal: object,
        state: object,
    ) -> ProcessedSignal:
        """
        Validasi signal melalui semua Portfolio Gate.

        Args:
            signal: Signal dari generate_signal()
            state: MarketState saat ini (berisi equity, open_positions, dll)

        Returns:
            ProcessedSignal dengan result APPROVED atau BLOCKED_*
        """
        symbol: str = getattr(signal, "symbol", "")
        side: str = getattr(signal, "side", "BUY")
        strat_id: str = getattr(signal, "strategy_id", "unknown")
        equity: float = getattr(state, "equity", 0.0)
        open_positions: dict = getattr(state, "open_positions", {})

        # ── Gate 0: Safety check (MarketState level) ──────────────────────
        if not getattr(state, "is_safe_to_trade", True):
            return ProcessedSignal(
                signal=signal,
                result=ProcessResult.BLOCKED_SAFETY,
                reason="MarketState.is_safe_to_trade is False (anomaly active).",
            )

        # ── Gate 1: Capital Manager ────────────────────────────────────────
        cap_status = getattr(self._capital, "get_status", lambda *a: None)(equity)
        if cap_status is not None:
            safe = getattr(cap_status, "safe_to_trade", True)
            if not safe:
                warnings = getattr(cap_status, "warnings", [])
                return ProcessedSignal(
                    signal=signal,
                    result=ProcessResult.BLOCKED_CAPITAL,
                    reason=f"CapitalManager: safe_to_trade=False. {'; '.join(warnings)}",
                )

        # ── Estimasi Notional (sebelum sizing resmi dari risk_layer) ──────
        estimated_notional = self._estimate_notional(equity, signal, open_positions)

        # ── Gate 2: Portfolio Allocation ───────────────────────────────────
        try:
            ok, reason = self._allocator.can_open(  # type: ignore[attr-defined]
                strat_id, symbol, estimated_notional, equity, open_positions
            )
            if not ok:
                return ProcessedSignal(
                    signal=signal,
                    result=ProcessResult.BLOCKED_ALLOCATION,
                    reason=f"Allocator: {reason}",
                    estimated_notional=estimated_notional,
                )
        except Exception as exc:
            logger.error(f"[SignalProcessor] Allocator check failed: {exc}")
            return ProcessedSignal(signal=signal, result=ProcessResult.ERROR, reason=str(exc))

        # ── Gate 3: Correlation ────────────────────────────────────────────
        try:
            ok, reason = self._correlation.check(  # type: ignore[attr-defined]
                symbol, side, estimated_notional, open_positions, equity
            )
            if not ok:
                return ProcessedSignal(
                    signal=signal,
                    result=ProcessResult.BLOCKED_CORRELATION,
                    reason=f"Correlation: {reason}",
                    estimated_notional=estimated_notional,
                )
        except Exception as exc:
            logger.error(f"[SignalProcessor] Correlation check failed: {exc}")
            return ProcessedSignal(signal=signal, result=ProcessResult.ERROR, reason=str(exc))

        # ── Gate 4: Risk Budget ────────────────────────────────────────────
        # Estimasi risk USD = distance (entry - SL) * qty proxy
        suggested_sl: float = getattr(signal, "suggested_sl", 0.0)
        last_price: float = getattr(state, "last_price", 0.0)
        estimated_risk_usd = self._estimate_risk_usd(last_price, suggested_sl, estimated_notional)

        try:
            ok, reason = self._risk_budget.can_take_risk(  # type: ignore[attr-defined]
                strat_id, estimated_risk_usd, equity
            )
            if not ok:
                return ProcessedSignal(
                    signal=signal,
                    result=ProcessResult.BLOCKED_BUDGET,
                    reason=f"RiskBudget: {reason}",
                    estimated_notional=estimated_notional,
                    risk_amount_usd=estimated_risk_usd,
                )
        except Exception as exc:
            logger.error(f"[SignalProcessor] RiskBudget check failed: {exc}")
            return ProcessedSignal(signal=signal, result=ProcessResult.ERROR, reason=str(exc))

        # ── APPROVED ──────────────────────────────────────────────────────
        logger.info(
            f"[SignalProcessor] APPROVED: {strat_id} {side} {symbol} "
            f"notional≈{estimated_notional:.0f} USDT, risk≈{estimated_risk_usd:.2f} USD."
        )

        if self._registry:
            self._registry.record_signal_approved(strat_id)

        return ProcessedSignal(
            signal=signal,
            result=ProcessResult.APPROVED,
            reason="",
            estimated_notional=estimated_notional,
            risk_amount_usd=estimated_risk_usd,
            approved_at=_utcnow(),
        )

    # ── Private Helpers ────────────────────────────────────────────────────

    def _estimate_notional(
        self,
        equity: float,
        signal: object,
        open_positions: dict,
    ) -> float:
        """
        Estimasi notional sebelum position sizer resmi berjalan.
        Gunakan risk_per_trade_pct × equity / sl_distance_pct sebagai proxy.
        Default: 5% equity sebagai estimasi konservatif.
        """
        risk_pct = getattr(getattr(self._allocator, "config", object()), "risk_per_trade_pct", 0.01)
        last_price = getattr(signal, "suggested_price", 0.0) or 1.0
        suggested_sl = getattr(signal, "suggested_sl", 0.0)

        if last_price > 0 and suggested_sl > 0:
            sl_pct = abs(last_price - suggested_sl) / last_price
            if sl_pct > 0:
                # Position sizing sederhana: risk_usd / sl_pct
                risk_usd = equity * risk_pct
                return min(risk_usd / sl_pct, equity * 0.20)  # Cap 20% equity

        # Fallback: estimasi 5% equity
        return equity * 0.05

    def _estimate_risk_usd(
        self,
        entry_price: float,
        sl_price: float,
        notional: float,
    ) -> float:
        """
        Hitung USD yang benar-benar di-risk pada trade ini.
        Formula: |entry - SL| / entry × notional
        """
        if entry_price <= 0 or sl_price <= 0 or notional <= 0:
            return notional * 0.02  # Fallback 2% dari notional

        sl_distance_pct = abs(entry_price - sl_price) / entry_price
        return notional * sl_distance_pct
