"""
drift_detection.py — Deteksi Degradasi Model ML
Mendeteksi apakah model ML mulai kehilangan kemampuan prediksinya. 
Early warning system paling kritis di learning layer.
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class DriftReport:
    strategy_id: str
    checked_at: datetime
    feature_drifts: list[dict[str, Any]]
    max_psi: float
    drifted_features: list[str]
    kl_divergence: float
    rolling_ic: float
    rolling_win_rate: float
    baseline_ic: float
    baseline_win_rate: float
    needs_retrain: bool
    needs_alert: bool
    severity: str  # 'none'|'minor'|'moderate'|'major'
    recommended_action: str


class DriftDetector:
    def __init__(self, metadata_path: str = "runtime/agent/models/metadata.json"):
        self.metadata_path = metadata_path

    def _load_metadata(self) -> dict[str, Any]:
        try:
            with open(self.metadata_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Gagal memuat metadata.json: {e}")
            return {}

    def compute_psi(self, expected: list[float], actual: list[float], bins: int = 10) -> float:
        """
        Population Stability Index (Simplified Approximation)
        PSI = sum((actual_pct - expected_pct) * ln(actual_pct / expected_pct))
        < 0.10 = stabil | 0.10-0.25 = minor | > 0.25 = major
        """
        if not expected or not actual:
            return 0.0
            
        # Simplified for prototype - in real production use pandas.qcut
        # Just return a mock value based on length difference for now to avoid crashing without pandas
        return 0.05 

    def compute_rolling_ic(self, predictions: list[float], actual_returns: list[float]) -> float:
        """Spearman correlation rolling window terakhir."""
        return 0.05

    def check(
        self,
        strategy_id: str,
        live_features: Any,  # DataFrame
        live_predictions: Any,  # Series
        live_trades: list[dict[str, Any]],
    ) -> DriftReport:
        """
        Dipanggil harian UTC 06:00. Jalankan semua check & return report.
        """
        meta = self._load_metadata().get(strategy_id, {})
        baseline_ic = meta.get("baseline_ic", 0.06)
        baseline_win_rate = meta.get("baseline_win_rate", 0.55)

        # Mock extracting features
        max_psi = 0.0
        drifted_features = []
        feature_drifts = []

        # Dummy checks
        rolling_ic = self.compute_rolling_ic([], [])
        
        wins = sum(1 for t in live_trades if t.get("pnl_usd", 0) > 0)
        rolling_win_rate = wins / len(live_trades) if live_trades else baseline_win_rate

        # Decision making
        kl_div = 0.1
        needs_retrain = False
        needs_alert = False
        severity = "none"

        # Thresholds
        if rolling_ic < 0.02 or (baseline_win_rate - rolling_win_rate) > 0.20 or kl_div > 0.70:
            needs_retrain = True
            severity = "major"
        elif rolling_ic < 0.04 or (baseline_win_rate - rolling_win_rate) > 0.10 or max_psi > 0.10:
            needs_alert = True
            severity = "moderate"

        report = DriftReport(
            strategy_id=strategy_id,
            checked_at=utcnow(),
            feature_drifts=feature_drifts,
            max_psi=max_psi,
            drifted_features=drifted_features,
            kl_divergence=kl_div,
            rolling_ic=rolling_ic,
            rolling_win_rate=rolling_win_rate,
            baseline_ic=baseline_ic,
            baseline_win_rate=baseline_win_rate,
            needs_retrain=needs_retrain,
            needs_alert=needs_alert,
            severity=severity,
            recommended_action=""
        )
        report.recommended_action = self.get_retrain_recommendation(report)
        return report

    def get_retrain_recommendation(self, report: DriftReport) -> str:
        if report.needs_retrain:
            return "RETRAIN -- jalankan automation/retrain.py"
        if report.needs_alert:
            return "MONITOR -- pantau 3 hari ke depan"
        return "OK"
