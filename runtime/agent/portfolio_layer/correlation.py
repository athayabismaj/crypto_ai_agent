"""
correlation.py — Kontrol Korelasi Pasar (Portfolio Layer)

Mencegah over-concentration pada aset yang bergerak bersama, karena
jika BTCUSDT dan ETHUSDT keduanya dalam posisi LONG bersamaan, kerugian
bisa terganda saat pasar merosot tajam.
"""

from typing import Any

# Default Static Groups
DEFAULT_CORRELATION_GROUPS: list[dict[str, Any]] = [
    {
        "id": "btc_eth",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "corr": 0.85,
        "max_combined_pct": 0.35,  # max 35% equity kombinasi
    },
    {
        "id": "eth_alts",
        "symbols": ["ETHUSDT", "BNBUSDT", "SOLUSDT"],
        "corr": 0.75,
        "max_combined_pct": 0.40,
    },
    {
        "id": "large_caps",
        "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"],
        "corr": 0.65,
        "max_combined_pct": 0.60,
    },
]


class CorrelationController:
    """Manajer eksposur uang untuk instrumen aset berkorelasi."""

    def __init__(self, groups: list[dict[str, Any]] | None = None) -> None:
        self._groups = groups if groups is not None else DEFAULT_CORRELATION_GROUPS

    def check(self, new_symbol: str, new_side: str, new_notional: float, positions: dict, equity: float) -> tuple[bool, str]:  # type: ignore
        """
        Cek apakah membuka posisi baru ini akan melanggar batas grup korelasi.
        Warning: Hanya bereaksi pada side yang SAMA.
        (Misal BTC Long + ETH Long dihitung. BTC Long + ETH Short saling ber-hedge, jadi aman).
        """
        if equity <= 0:
            return False, "Equity is zero"

        for group in self._groups:
            if new_symbol not in group["symbols"]:
                continue

            # Hitung current combined exposure yang berada dalam arah 'side' yang sama di grup tsb
            current_combined = 0.0
            for sym, pos in positions.items():
                if sym in group["symbols"] and getattr(pos, "side", "BUY") == new_side:
                    # Ambil valuasinya (notional = qty * entry)
                    qty = getattr(pos, "filled_qty", 0.0)
                    entry_price = getattr(pos, "avg_fill_price", 0.0)
                    current_combined += qty * entry_price

            proposed_exposure = (current_combined + new_notional) / equity

            if proposed_exposure > group["max_combined_pct"]:
                return False, (
                    f"Correlation group '{group['id']}' limit: "
                    f"{group['max_combined_pct']:.0%} equity. "
                    f"Current={current_combined/equity:.1%}, "
                    f"Proposed={proposed_exposure:.1%}"
                )

        return True, ""

    def get_group_exposure(self, group_id: str, positions: dict, equity: float) -> float:  # type: ignore
        """Return % equity yang terexpose di grup korelasi tertentu (kombinasi absolut)."""
        if equity <= 0:
            return 0.0

        target_group = next((g for g in self._groups if g["id"] == group_id), None)
        if not target_group:
            return 0.0

        current_combined = 0.0
        for sym, pos in positions.items():
            if sym in target_group["symbols"]:
                qty = getattr(pos, "filled_qty", 0.0)
                entry_price = getattr(pos, "avg_fill_price", 0.0)
                current_combined += qty * entry_price

        return current_combined / equity

    def update_groups(self, new_groups: list[dict[str, Any]]) -> None:
        """Update limit correlation groups dari YAML (tanpa me-restart script)."""
        self._groups = new_groups
