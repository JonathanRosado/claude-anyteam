from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_claude_dir(tmp_path: Path) -> Path:
    teams_dir = tmp_path / "teams"
    teams_dir.mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    return tmp_path
