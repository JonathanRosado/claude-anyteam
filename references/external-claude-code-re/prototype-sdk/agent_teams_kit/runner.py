from __future__ import annotations

import sys

from .lifecycle import run
from .teammate import Teammate


def main(teammate_cls: type[Teammate]) -> None:
    sys.exit(run(teammate_cls))
