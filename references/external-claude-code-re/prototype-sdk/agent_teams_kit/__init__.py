"""Prototype Agent Teams kit SDK (research artifact, not production)."""

from .capabilities import CapabilityEntry, CapabilityManifest
from .events import VisibilityEvent
from .messages import TaskResult, parse_protocol_text
from .storage import FilesystemStorage, TeamStorage
from .team import Team
from .teammate import Teammate
from .lifecycle import run

__all__ = [
    "CapabilityEntry",
    "CapabilityManifest",
    "FilesystemStorage",
    "TaskResult",
    "Team",
    "Teammate",
    "TeamStorage",
    "VisibilityEvent",
    "parse_protocol_text",
    "run",
]
