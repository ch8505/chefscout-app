from __future__ import annotations

import pytest

from chefscout.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Isolated settings: temp data dir, no key, and no .env from the developer's machine."""
    return Settings(_env_file=None, data_dir=tmp_path / "data")
