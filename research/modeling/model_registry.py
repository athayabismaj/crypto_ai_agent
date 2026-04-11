"""
model_registry.py — Versioning & Tracking Model
Setiap model punya metadata lengkap + status lifecycle.
Bisa di-rollback kapan saja.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

REGISTRY_DIR = "research/models/registry"
ARCHIVE_DIR = "research/models/archive"


@dataclass
class ModelMetadata:
    model_id: str
    version: str
    created_at: str
    model_type: str
    target_col: str
    symbol: str
    timeframe: str
    train_period: list[str]
    feature_names: list[str]
    feature_count: int
    metrics: dict[str, float]
    status: str = "candidate"  # candidate → validated → production → deprecated → archived → failed
    replaces: str = ""
    pkl_path: str = ""


# Status lifecycle yang sah
VALID_TRANSITIONS = {
    "candidate": ["validated", "failed"],
    "validated": ["production", "failed"],
    "production": ["deprecated"],
    "deprecated": ["archived"],
    "failed": [],
    "archived": [],
}


class ModelRegistry:
    """Mengelola semua model yang pernah di-training."""

    def __init__(self, registry_dir: str = REGISTRY_DIR, archive_dir: str = ARCHIVE_DIR):
        self.registry_dir = Path(registry_dir)
        self.archive_dir = Path(archive_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, model_id: str) -> Path:
        return self.registry_dir / f"{model_id}_metadata.json"

    def register(self, meta: ModelMetadata) -> Path:
        """Daftarkan model baru ke registry."""
        path = self._meta_path(meta.model_id)
        data = asdict(meta)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        log.info(f"Model registered: {meta.model_id} (status={meta.status})")
        return path

    def get(self, model_id: str) -> ModelMetadata | None:
        """Ambil metadata model berdasarkan ID."""
        path = self._meta_path(model_id)
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
        return ModelMetadata(**data)

    def update_status(self, model_id: str, new_status: str) -> bool:
        """Transisi status model. Enforce lifecycle yang sah."""
        meta = self.get(model_id)
        if meta is None:
            log.error(f"Model {model_id} tidak ditemukan di registry.")
            return False

        valid_next = VALID_TRANSITIONS.get(meta.status, [])
        if new_status not in valid_next:
            log.error(f"Transisi tidak sah: {meta.status} → {new_status}. " f"Valid: {valid_next}")
            return False

        meta.status = new_status
        self.register(meta)  # overwrite
        log.info(f"Model {model_id}: {meta.status} → {new_status}")
        return True

    def get_production_model(self, symbol: str = "", market_type: str = "") -> ModelMetadata | None:
        """Cari model yang sedang dalam status 'production'."""
        for path in self.registry_dir.glob("*_metadata.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                if data.get("status") == "production":
                    if symbol and data.get("symbol") != symbol:
                        continue
                    return ModelMetadata(**data)
            except Exception:
                continue
        return None

    def list_models(self, status: str | None = None) -> list[ModelMetadata]:
        """List semua model, opsional filter by status."""
        models: list[ModelMetadata] = []
        for path in sorted(self.registry_dir.glob("*_metadata.json")):
            try:
                with open(path) as f:
                    data = json.load(f)
                meta = ModelMetadata(**data)
                if status is None or meta.status == status:
                    models.append(meta)
            except Exception as e:
                log.warning(f"Skip corrupt metadata {path}: {e}")
        return models

    def archive_model(self, model_id: str) -> bool:
        """Pindahkan metadata dan pkl ke archive."""
        meta = self.get(model_id)
        if meta is None:
            return False

        # Copy metadata ke archive
        src_meta = self._meta_path(model_id)
        dst_meta = self.archive_dir / src_meta.name
        shutil.copy2(src_meta, dst_meta)

        # Copy pkl jika ada
        if meta.pkl_path and Path(meta.pkl_path).exists():
            dst_pkl = self.archive_dir / Path(meta.pkl_path).name
            shutil.copy2(meta.pkl_path, dst_pkl)

        # Update status
        self.update_status(model_id, "archived") if meta.status == "deprecated" else None

        log.info(f"Model {model_id} di-archive ke {self.archive_dir}")
        return True

    def deprecate_and_replace(self, old_id: str, new_id: str) -> bool:
        """Deprecate model lama dan jadikan model baru sebagai production."""
        old = self.get(old_id)
        new = self.get(new_id)

        if old is None or new is None:
            log.error(f"Model {old_id} atau {new_id} tidak ditemukan.")
            return False

        # Deprecate old
        if old.status == "production":
            self.update_status(old_id, "deprecated")

        # Promote new
        new.replaces = old_id
        self.register(new)
        if new.status == "validated":
            self.update_status(new_id, "production")

        log.info(f"Replaced {old_id} → {new_id}")
        return True


def generate_model_id(model_type: str, version: str, suffix: str = "") -> str:
    """Generate ID konsisten: spot_lgbm_v1_0_0"""
    ver_clean = version.replace(".", "_")
    parts = [suffix, model_type, f"v{ver_clean}"]
    return "_".join(p for p in parts if p)
