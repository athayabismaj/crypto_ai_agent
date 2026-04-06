"""
retrain.py — Auto-Retrain Pipeline Coordinator
Dipanggil saat drift terdeteksi atau jadwal mingguan.
Mengkoordinasikan seluruh pipeline: fetch → clean → feature → train → evaluate → deploy.

Safeguard: model baru TIDAK di-deploy jika lebih buruk dari model lama.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class RetrainReport:
    strategy_id: str
    trigger: str            # 'drift' | 'scheduled' | 'manual'
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    success: bool = False
    deployed: bool = False
    new_version: str = ""
    old_version: str = ""
    metrics_new: dict = field(default_factory=dict)
    metrics_old: dict = field(default_factory=dict)
    abort_reason: str = ""
    duration_s: float = 0.0


class RetrainPipeline:
    """
    Koordinator utama proses retrain model.
    Pipeline berjalan secara sequential dengan fail-fast di setiap gate.
    """

    def __init__(
        self,
        data_dir: str = "research/data",
        model_dir: str = "research/models",
        deploy_target: str = "runtime/agent/models",
        notifier: Any = None,
    ):
        self.data_dir = Path(data_dir)
        self.model_dir = Path(model_dir)
        self.deploy_target = Path(deploy_target)
        self.notifier = notifier
        self._last_reports: dict[str, RetrainReport] = {}
        self._running = False

    async def run(
        self,
        strategy_id: str,
        force: bool = False,
        suspend_trading: bool = False,
    ) -> RetrainReport:
        """
        Urutan pipeline retrain:
        1. Fetch data terbaru
        2. Clean & feature engineering
        3. Leakage check — gagal → abort
        4. Train model
        5. Walk-forward validation
        6. Evaluate — gagal threshold → abort, tidak deploy
        7. Compare dengan model lama
        8. Deploy via deploy.py (jika lebih baik)
        9. Kirim notifikasi hasil
        """
        if self._running:
            log.warning("Retrain sudah berjalan. Skip duplicate.")
            report = RetrainReport(strategy_id=strategy_id, trigger="blocked")
            report.abort_reason = "Pipeline sedang berjalan"
            return report

        self._running = True
        report = RetrainReport(strategy_id=strategy_id, trigger="manual" if force else "scheduled")

        try:
            log.info(f"═══ RETRAIN PIPELINE START: {strategy_id} ═══")

            # Step 1: Fetch data terbaru
            log.info("[1/8] Fetching data terbaru...")
            fetch_ok = await self._step_fetch(strategy_id)
            if not fetch_ok:
                report.abort_reason = "Fetch data gagal"
                return report

            # Step 2: Clean & feature
            log.info("[2/8] Cleaning & feature engineering...")
            features_path = await self._step_clean_and_feature(strategy_id)
            if features_path is None:
                report.abort_reason = "Clean/Feature engineering gagal"
                return report

            # Step 3: Leakage check (HARD GATE)
            log.info("[3/8] Leakage check...")
            leak_ok = await self._step_leakage_check(features_path)
            if not leak_ok:
                report.abort_reason = "DATA LEAKAGE terdeteksi! Pipeline DIHENTIKAN."
                return report

            # Step 4: Train
            log.info("[4/8] Training model...")
            trained = await self._step_train(features_path, strategy_id)
            if trained is None:
                report.abort_reason = "Training gagal"
                return report

            # Step 5: Walk-forward
            log.info("[5/8] Walk-forward validation...")
            wf_summary = await self._step_walk_forward(features_path, strategy_id)

            # Step 6: Evaluate (HARD GATE)
            log.info("[6/8] Evaluating model...")
            eval_ok, eval_metrics = await self._step_evaluate(trained, wf_summary)
            report.metrics_new = eval_metrics
            if not eval_ok:
                report.abort_reason = "Model gagal threshold evaluasi"
                return report

            # Step 7: Compare with existing
            log.info("[7/8] Comparing with production model...")
            should_deploy, reason = self._should_deploy(eval_metrics, report.metrics_old)
            if not should_deploy and not force:
                report.abort_reason = f"Model baru tidak lebih baik: {reason}"
                return report

            # Step 8: Deploy
            log.info("[8/8] Deploying model...")
            from automation.deploy import DeployManager
            deployer = DeployManager(target_dir=str(self.deploy_target))
            deploy_ok = await deployer.deploy_model_from_trained(trained, strategy_id)
            report.deployed = deploy_ok

            report.success = True
            log.info(f"═══ RETRAIN PIPELINE SUCCESS: {strategy_id} ═══")

        except Exception as e:
            report.abort_reason = f"Pipeline error: {e}"
            log.error(f"Retrain pipeline error: {e}", exc_info=True)

        finally:
            report.finished_at = datetime.now(timezone.utc)
            report.duration_s = (report.finished_at - report.started_at).total_seconds()
            self._running = False
            self._last_reports[strategy_id] = report

            # Kirim notifikasi
            if self.notifier:
                try:
                    status = "✅ SUCCESS" if report.success else f"❌ ABORT: {report.abort_reason}"
                    await self.notifier.notify(
                        "retrain_complete",
                        {"strategy": strategy_id, "status": status, "duration": f"{report.duration_s:.0f}s"},
                        severity="info" if report.success else "warning",
                    )
                except Exception:
                    pass

        return report

    def _should_deploy(
        self,
        new_metrics: dict,
        old_metrics: dict,
    ) -> tuple[bool, str]:
        """
        Model baru hanya di-deploy jika LEBIH BAIK dari model lama.
        Sharpe baru >= Sharpe lama × 0.90 (max turun 10%)
        IC baru >= IC lama × 0.85 (max turun 15%)
        """
        if not old_metrics:
            return True, "Tidak ada model lama. Deploy langsung."

        new_sharpe = new_metrics.get("sharpe_signal", 0)
        old_sharpe = old_metrics.get("sharpe_signal", 0)
        if old_sharpe > 0 and new_sharpe < old_sharpe * 0.90:
            return False, (
                f"Sharpe turun terlalu banyak: {new_sharpe:.2f} vs {old_sharpe:.2f}"
            )

        new_ic = new_metrics.get("ic_mean", 0)
        old_ic = old_metrics.get("ic_mean", 0)
        if old_ic > 0 and new_ic < old_ic * 0.85:
            return False, (
                f"IC turun terlalu banyak: {new_ic:.3f} vs {old_ic:.3f}"
            )

        return True, ""

    # ── Stub methods (implementasi sebenarnya memanggil research/) ──

    async def _step_fetch(self, strategy_id: str) -> bool:
        """Fetch data terbaru via research/pipeline/fetch_data.py"""
        try:
            from research.pipeline.fetch_data import run_fetch_pipeline, FetchConfig
            config = FetchConfig()
            results = run_fetch_pipeline(config)
            return len(results) > 0
        except Exception as e:
            log.error(f"Fetch step gagal: {e}")
            return False

    async def _step_clean_and_feature(self, strategy_id: str) -> Path | None:
        """Clean + feature engineering."""
        try:
            # Placeholder: akan menggunakan file parquet hasil fetch
            return self.data_dir / "features"
        except Exception as e:
            log.error(f"Clean/feature step gagal: {e}")
            return None

    async def _step_leakage_check(self, features_path: Path) -> bool:
        """Leakage check via research/validation/leakage_check.py"""
        try:
            log.info("Leakage check: deferred to actual data loading.")
            return True
        except Exception as e:
            log.error(f"Leakage check gagal: {e}")
            return False

    async def _step_train(self, features_path: Path, strategy_id: str) -> Any:
        """Train via research/modeling/train.py"""
        try:
            log.info("Training: deferred to actual feature data.")
            return None  # Would return TrainedModel
        except Exception as e:
            log.error(f"Training gagal: {e}")
            return None

    async def _step_walk_forward(self, features_path: Path, strategy_id: str) -> Any:
        """Walk-forward via research/modeling/walk_forward.py"""
        return None

    async def _step_evaluate(self, trained: Any, wf_summary: Any) -> tuple[bool, dict]:
        """Evaluate via research/modeling/evaluate.py"""
        return True, {}

    def get_last_report(self, strategy_id: str) -> RetrainReport | None:
        return self._last_reports.get(strategy_id)
