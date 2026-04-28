"""Pytest fixtures shared across the suite.

Sets CLAUDE_ANYTEAM_NO_COLOR=1 so the installer's theme/box rendering
falls back to plain text in tests. The `_theme._supports_color` cascade
is intentionally permissive in production (FORCE_COLOR, WT_SESSION,
CLAUDE_ANYTEAM_NPM_PARENT, isatty, etc. all flip color ON) so that real
users get the beautified UI through every spawn chain — including
npx → uv tool run on Windows where isatty() lies. Tests need the
opposite signal so substring assertions stay deterministic.
"""

import os

os.environ.setdefault("CLAUDE_ANYTEAM_NO_COLOR", "1")
