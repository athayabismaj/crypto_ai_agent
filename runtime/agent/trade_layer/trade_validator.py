"""
Validasi konsistensi sinyal dan pengecekan akhir sebelum trade dikirim ke exchange.
"""

from typing import Any

# Menghindari circular import
# Dalam real logic:
# from runtime.agent.models.signal import Signal
# from runtime.agent.models.risk import RiskResult


class TradeValidator:
    def __init__(self, config: Any):
        self.config = config

    def validate(self, signal: Any, risk_result: Any) -> tuple[bool, str]:
        """
        Validasi konsistensi antara signal dan hasil risk_layer.
        Jika tidak konsisten, blokir sebelum menjadi Trade.
        """
        if risk_result.approved_quantity <= 0:
            return (
                False,
                f"Approved quantity harus > 0 (Diberikan: {risk_result.approved_quantity})",
            )

        if risk_result.sl_price <= 0 and signal.signal_type != "close_out":
            return False, "SL Price dari risk_result tidak valid / nol"

        min_conf = getattr(self.config, "min_signal_confidence", 0.5)
        if signal.final_confidence < min_conf:
            return (
                False,
                f"Confidence {signal.final_confidence:.2f} di bawah minimum {min_conf:.2f}",
            )

        # Mode consistency
        signal_mode = getattr(signal, "mode", "live")
        config_mode = getattr(self.config, "mode", "live")

        if config_mode == "live" and signal_mode == "paper":
            return False, "Mode mismatch: live config tapi paper signal"

        return True, ""
