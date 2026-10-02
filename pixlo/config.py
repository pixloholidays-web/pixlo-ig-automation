"""Config + environment helpers."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent

try:  # optional: load .env for local runs
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg.get("timezone", "Asia/Kolkata"))


def env(name: str, required: bool = False, default: str | None = None) -> str | None:
    value = os.getenv(name) or default
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value
