"""
Lapis 3: LeverageControl
Mencegah penggunaan leverage berlebihan pada futures trade request.
"""
from runtime.agent.core.config_schema import AgentConfig  # type: ignore

# Leverage limits default
SYMBOL_MAX_LEVERAGE = {
    "BTCUSDT": 10,
    "ETHUSDT": 10,
    "BNBUSDT": 5,
    "SOLUSDT": 5,
    "DEFAULT": 3,
}


class LeverageControl:
    def __init__(self, config: AgentConfig):
        self.global_max = getattr(config, "global_max_leverage", 5)
        # Jika ada override symbol leverage dari config dict
        cf_symbol_lev = getattr(config, "symbol_max_leverage", {})
        self.symbol_max_leverage = {**SYMBOL_MAX_LEVERAGE, **cf_symbol_lev}

    def validate(self, leverage: int, symbol: str) -> tuple[bool, str]:
        """Validasi limit."""
        if leverage <= 0:
            return False, f"Leverage tidak valid: {leverage}"

        symbol_max = self.symbol_max_leverage.get(
            symbol, self.symbol_max_leverage.get("DEFAULT", 3)
        )
        effective_max = min(self.global_max, symbol_max)

        if leverage > effective_max:
            return False, (
                f"Leverage {leverage}x melebihi batas {effective_max}x "
                f"untuk {symbol} (global={self.global_max}, symbol={symbol_max})"
            )

        return True, ""

    def get_max_leverage(self, symbol: str) -> int:
        symbol_max = self.symbol_max_leverage.get(
            symbol, self.symbol_max_leverage.get("DEFAULT", 3)
        )
        return min(self.global_max, symbol_max)

    def get_effective_leverage(self, notional: float, margin: float) -> float:
        if margin <= 0:
            return 0.0
        return notional / margin
