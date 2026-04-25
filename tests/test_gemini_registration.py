from __future__ import annotations

import json
from pathlib import Path

from claude_anyteam.backends.gemini.config import GeminiSettings
from claude_anyteam.registration import BackendMetadata, register
from claude_anyteam import registration as registration_mod


def test_gemini_registration_metadata(tmp_path, monkeypatch):
    root = tmp_path / "teams"
    monkeypatch.setattr(registration_mod, "TEAMS_ROOT", root)
    cfg = root / "t" / "config.json"
    cfg.parent.mkdir(parents=True)
    (root / "t" / "inboxes").mkdir()
    cfg.write_text(json.dumps({"members": []}))
    settings = GeminiSettings("t", "gemini-a", tmp_path, 1.0, "cyan", False)

    entry = register(settings, BackendMetadata(model="gemini-cli", prompt="Gemini CLI headless adapter"))

    assert entry["model"] == "gemini-cli"
    assert "Gemini" in entry["prompt"]
    assert (root / "t" / "inboxes" / "gemini-a.json").exists()
