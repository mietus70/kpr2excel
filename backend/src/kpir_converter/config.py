"""Local application configuration.

No secrets, no remote endpoints. Everything is read from the environment with
safe local defaults. The data directory lives in the platform user-data location,
never inside the repository.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

__all__ = ["Settings", "get_settings", "default_data_dir"]


def default_data_dir() -> Path:
    override = os.environ.get("KPIR_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "kpir-converter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kpir-converter"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "kpir-converter"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    api_host: str = field(default_factory=lambda: os.environ.get("KPIR_API_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: _env_int("KPIR_API_PORT", 8756))
    frontend_port: int = field(default_factory=lambda: _env_int("KPIR_FRONTEND_PORT", 5173))
    max_upload_mb: int = field(default_factory=lambda: _env_int("KPIR_MAX_UPLOAD_MB", 200))
    max_pages_per_document: int = field(
        default_factory=lambda: _env_int("KPIR_MAX_PAGES_PER_DOCUMENT", 5000)
    )
    worker_concurrency: int = field(default_factory=lambda: _env_int("KPIR_WORKER_CONCURRENCY", 1))
    log_level: str = field(default_factory=lambda: os.environ.get("KPIR_LOG_LEVEL", "INFO"))
    render_dpi: int = field(default_factory=lambda: _env_int("KPIR_RENDER_DPI", 130))
    dev_mode: bool = field(default_factory=lambda: _env_bool("KPIR_DEV_MODE", False))
    profiles_dir: Path | None = None

    # --------------------------------------------------------------- derived paths
    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def pages_dir(self) -> Path:
        return self.data_dir / "pages"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "temp"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def resolved_profiles_dir(self) -> Path:
        if self.profiles_dir is not None:
            return self.profiles_dir
        env = os.environ.get("KPIR_PROFILES_DIR")
        if env:
            return Path(env).expanduser().resolve()
        # repository layout: backend/src/kpir_converter/config.py -> <repo>/profiles
        return Path(__file__).resolve().parents[3] / "profiles"

    def allowed_origins(self) -> list[str]:
        """Dev-only explicit Vite origins; the packaged build is same-origin."""
        if not self.dev_mode:
            return []
        hosts = ["127.0.0.1", "localhost"]
        return [f"http://{h}:{self.frontend_port}" for h in hosts]

    def allowed_hosts(self) -> set[str]:
        hosts = {
            f"127.0.0.1:{self.api_port}",
            f"localhost:{self.api_port}",
            "127.0.0.1",
            "localhost",
            "testserver",
        }
        extra = os.environ.get("KPIR_EXTRA_ALLOWED_HOSTS", "")
        for item in extra.split(","):
            if item.strip():
                hosts.add(item.strip())
        return hosts

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.originals_dir,
            self.pages_dir,
            self.exports_dir,
            self.temp_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
