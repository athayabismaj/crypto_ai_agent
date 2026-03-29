"""
Lapis 4: ExposureControl
Mencegah konsentrasi ekstrem atau limit posisi berlebihan pada trade request.
"""
from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore

CORRELATED_PAIRS: list[set[str]] = [
    {"BTCUSDT", "ETHUSDT"},
    {"ETHUSDT", "BNBUSDT"},
    {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"},
]


class ExposureControl:
    def __init__(self, config: AgentConfig):
        self.max_positions = getattr(config, "max_open_positions", 5)
        self.max_per_symbol = getattr(config, "max_exposure_per_symbol_pct", 0.20)
        self.max_total = getattr(config, "max_total_exposure_pct", 0.80)
        self.max_correlated = getattr(config, "max_correlated_exposure_pct", 0.35)

    def validate(self, request: TradeRequest, portfolio_state: dict) -> tuple[bool, str]:
        equity = portfolio_state.get("equity", 1.0)
        positions = portfolio_state.get("open_positions", {})

        # Harga terkini
        price = request.price or portfolio_state.get(f"last_price_{request.symbol}", 0.0)
        new_notional = request.quantity * price

        # Cek 1: Jumlah max posisi terbuka
        if len(positions) >= self.max_positions:
            if request.symbol not in positions:
                return False, f"Max posisi ({self.max_positions}) sudah tercapai"

        # Cek 2: Max porsi per simbol
        existing = positions.get(request.symbol, {}).get("notional", 0.0)
        if (existing + new_notional) / equity > self.max_per_symbol:
            return False, (
                f"{request.symbol} exposure {(existing+new_notional)/equity:.1%} "
                f"> max {self.max_per_symbol:.1%}"
            )

        # Cek 3: Max total keseluruhan
        total_current = sum(p.get("notional", 0.0) for p in positions.values())
        if (total_current + new_notional) / equity > self.max_total:
            return False, (
                f"Total exposure {(total_current+new_notional)/equity:.1%} "
                f"> max {self.max_total:.1%}"
            )

        # Cek 4: Exposure grup berpasangan ber-korelasi kuat
        corr_result = self._check_correlation(request.symbol, new_notional, positions, equity)
        if corr_result:
            return False, corr_result

        return True, ""

    def _check_correlation(
        self, symbol: str, new_notional: float, positions: dict, equity: float
    ) -> str:
        for pair in CORRELATED_PAIRS:
            if symbol not in pair:
                continue

            partner_notional = sum(
                positions.get(s, {}).get("notional", 0) for s in pair if s != symbol
            )
            existing = positions.get(symbol, {}).get("notional", 0)
            total = partner_notional + existing + new_notional

            if total / equity > self.max_correlated:
                return (
                    f"Correlated exposure ({'+'.join(pair)}) "
                    f"{total/equity:.1%} > max {self.max_correlated:.1%}"
                )
        return ""
