import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore
from dotenv import load_dotenv  # type: ignore

from runtime.agent.core.config_schema import AgentConfig  # type: ignore


def load_config(yaml_path: str | None = None) -> AgentConfig:
    """
    Load configurasi dengan prioritas:
    1. Environment variables (.env)
    2. YAML config file
    3. Pydantic default values
    """
    load_dotenv()

    # Resolusi path YAML default jika tidak tersedia
    if not yaml_path:
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        yaml_path = str(base_dir / "config" / "agent_config.yaml")

    yaml_data: dict[str, Any] = {}
    if os.path.exists(yaml_path):
        with open(yaml_path, encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}

    # Mulai dengan YAML values
    config_dict: dict[str, Any] = {}
    config_dict.update(yaml_data)

    # Override dengan Environment variables (yang namanya sama, kapital)
    for key in AgentConfig.model_fields.keys():
        env_key = key.upper()
        env_val = os.getenv(env_key)

        if env_val is not None:
            # Special parsing untuk list (symbols) dan boolean
            if env_val.lower() == "true":
                config_dict[key] = True
            elif env_val.lower() == "false":
                config_dict[key] = False
            elif key == "symbols":
                config_dict[key] = [s.strip() for s in env_val.split(",") if s.strip()]
            else:
                config_dict[key] = env_val

    # Validasi dan instantiate Pydantic model
    return AgentConfig(**config_dict)
