from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kpir_converter.application.services import AppContext  # noqa: E402
from kpir_converter.config import Settings  # noqa: E402
from kpir_converter.infrastructure.db.database import Database  # noqa: E402
from kpir_converter.infrastructure.storage.profiles_loader import load_registry  # noqa: E402


@pytest.fixture(scope="session")
def profiles_dir() -> Path:
    return REPO_ROOT / "profiles"


@pytest.fixture(scope="session")
def profile(profiles_dir: Path):
    return load_registry(profiles_dir).get("kpir_pl_2018")


@pytest.fixture()
def settings(tmp_path: Path, profiles_dir: Path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", profiles_dir=profiles_dir)
    s.dev_mode = False
    s.ensure_directories()
    return s


@pytest.fixture()
def ctx(settings: Settings) -> AppContext:
    db = Database(settings.db_path)
    db.migrate()
    registry = load_registry(settings.resolved_profiles_dir())
    context = AppContext(settings=settings, db=db, profiles=registry)
    yield context
    db.close()


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    from fixtures.synthetic_kpir import build_synthetic_kpir

    return build_synthetic_kpir(tmp_path / "kpir.pdf")
