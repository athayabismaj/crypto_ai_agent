"""
deploy.py — Atomic Deploy Artefak ke Runtime
Memindahkan model (pkl + metadata) dari research ke runtime secara atomik.
Bisa rollback jika deploy gagal.

Deploy flow:
1. Validasi model baru
2. Backup model lama ke archive/
3. Copy model baru ke runtime/agent/models/
4. Update metadata.json
5. Smoke test (validasi load)
6. Rollback otomatis jika step 3-5 gagal
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class DeployReport:
    market_type: str
    old_version: str
    new_version: str
    success: bool
    deployed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rollback_path: str = ""
    error: str = ""


class DeployManager:
    """
    Atomic deploy manager — backup dulu, baru overwrite.
    Jika gagal di tengah jalan, rollback otomatis.
    """

    def __init__(
        self,
        target_dir: str = "runtime/agent/models",
        archive_dir: str = "runtime/agent/models/archive",
    ):
        self.target_dir = Path(target_dir)
        self.archive_dir = Path(archive_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    async def deploy_model(
        self,
        new_model_path: Path,
        new_meta_path: Path,
        market_type: str = "spot",
        notify: bool = True,
    ) -> DeployReport:
        """
        Deploy model baru secara atomik.
        """
        report = DeployReport(
            market_type=market_type,
            old_version="",
            new_version="",
            success=False,
        )

        target_pkl = self.target_dir / f"{market_type}_model.pkl"
        target_meta = self.target_dir / "metadata.json"
        backup_pkl: Path | None = None
        backup_meta: Path | None = None

        try:
            # Step 1: Validasi model baru ada
            if not new_model_path.exists():
                report.error = f"Model file tidak ditemukan: {new_model_path}"
                return report

            # Step 2: Baca versi model lama (jika ada)
            if target_meta.exists():
                with open(target_meta) as f:
                    old_meta = json.load(f)
                report.old_version = old_meta.get("version", "unknown")

            # Step 3: Backup model lama
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            if target_pkl.exists():
                backup_pkl = self.archive_dir / f"{market_type}_model_{timestamp}.pkl"
                shutil.copy2(target_pkl, backup_pkl)
                log.info(f"Backup model lama: {backup_pkl}")

            if target_meta.exists():
                backup_meta = self.archive_dir / f"metadata_{timestamp}.json"
                shutil.copy2(target_meta, backup_meta)
                log.info(f"Backup metadata lama: {backup_meta}")

            report.rollback_path = str(backup_pkl) if backup_pkl else ""

            # Step 4: Copy model baru
            shutil.copy2(new_model_path, target_pkl)
            log.info(f"Model baru deployed: {target_pkl}")

            # Step 5: Update metadata.json
            if new_meta_path.exists():
                with open(new_meta_path) as f:
                    new_meta = json.load(f)
                new_meta["deployed_at"] = datetime.now(timezone.utc).isoformat()
                new_meta["status"] = "production"
                report.new_version = new_meta.get("version", "unknown")
                with open(target_meta, "w") as f:
                    json.dump(new_meta, f, indent=2)
            else:
                # Buat metadata minimal
                meta = {
                    "market_type": market_type,
                    "deployed_at": datetime.now(timezone.utc).isoformat(),
                    "model_path": str(target_pkl),
                    "status": "production",
                }
                with open(target_meta, "w") as f:
                    json.dump(meta, f, indent=2)

            # Step 6: Smoke test — validasi model bisa di-load
            try:
                import joblib
                loaded = joblib.load(target_pkl)
                if loaded is None:
                    raise ValueError("Model load menghasilkan None")
                log.info("Smoke test PASSED: model berhasil di-load.")
            except Exception as e:
                log.error(f"Smoke test GAGAL: {e}. ROLLING BACK...")
                self._rollback(backup_pkl, target_pkl, backup_meta, target_meta)
                report.error = f"Smoke test gagal: {e}"
                return report

            report.success = True
            log.info(
                f"✅ Deploy berhasil: {market_type} "
                f"v{report.old_version} → v{report.new_version}"
            )

        except Exception as e:
            log.error(f"Deploy gagal: {e}. Rolling back...")
            self._rollback(backup_pkl, target_pkl, backup_meta, target_meta)
            report.error = str(e)

        return report

    async def deploy_model_from_trained(
        self,
        trained_model: Any,
        strategy_id: str,
    ) -> bool:
        """Shortcut: deploy dari TrainedModel object langsung."""
        if trained_model is None:
            return False
        try:
            import joblib
            import tempfile
            # Serialize ke temp, lalu deploy
            tmp_pkl = Path(tempfile.mktemp(suffix=".pkl"))
            joblib.dump(trained_model, tmp_pkl)

            tmp_meta = Path(tempfile.mktemp(suffix=".json"))
            meta = {
                "model_type": getattr(trained_model, "model_type", "unknown"),
                "version": getattr(trained_model, "version", "1.0.0"),
                "feature_names": getattr(trained_model, "feature_names", []),
                "strategy_id": strategy_id,
            }
            with open(tmp_meta, "w") as f:
                json.dump(meta, f, indent=2)

            report = await self.deploy_model(tmp_pkl, tmp_meta, market_type="spot")

            # Cleanup temp
            tmp_pkl.unlink(missing_ok=True)
            tmp_meta.unlink(missing_ok=True)

            return report.success
        except Exception as e:
            log.error(f"Deploy from trained gagal: {e}")
            return False

    def deploy_params(
        self,
        strategy_id: str,
        new_params_path: Path,
    ) -> bool:
        """Deploy best_params.json hasil optimasi ke runtime config."""
        try:
            target = self.target_dir / f"best_params_{strategy_id}.json"

            # Backup
            if target.exists():
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                backup = self.archive_dir / f"best_params_{strategy_id}_{ts}.json"
                shutil.copy2(target, backup)

            shutil.copy2(new_params_path, target)
            log.info(f"Params deployed: {target}")
            return True
        except Exception as e:
            log.error(f"Deploy params gagal: {e}")
            return False

    def rollback_model(
        self,
        market_type: str = "spot",
        version: str | None = None,
    ) -> bool:
        """Rollback ke versi model sebelumnya dari archive."""
        try:
            # Cari backup terbaru
            pattern = f"{market_type}_model_*.pkl"
            backups = sorted(self.archive_dir.glob(pattern), reverse=True)

            if not backups:
                log.error(f"Tidak ada backup {market_type} untuk rollback.")
                return False

            # Pilih versi tertentu atau terbaru
            backup = backups[0]
            if version:
                for b in backups:
                    if version in b.name:
                        backup = b
                        break

            target = self.target_dir / f"{market_type}_model.pkl"
            shutil.copy2(backup, target)
            log.info(f"Rollback berhasil: {backup.name} → {target}")
            return True
        except Exception as e:
            log.error(f"Rollback gagal: {e}")
            return False

    def list_available_versions(self, market_type: str = "spot") -> list[str]:
        """List semua versi yang tersedia di archive."""
        pattern = f"{market_type}_model_*.pkl"
        return sorted([p.stem for p in self.archive_dir.glob(pattern)], reverse=True)

    def _rollback(
        self,
        backup_pkl: Path | None,
        target_pkl: Path,
        backup_meta: Path | None,
        target_meta: Path,
    ) -> None:
        """Internal rollback ke backup."""
        if backup_pkl and backup_pkl.exists():
            shutil.copy2(backup_pkl, target_pkl)
            log.info(f"Rollback model: {backup_pkl} → {target_pkl}")
        if backup_meta and backup_meta.exists():
            shutil.copy2(backup_meta, target_meta)
            log.info(f"Rollback metadata: {backup_meta} → {target_meta}")
