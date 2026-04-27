from __future__ import annotations

import asyncio

from .teammate import Teammate


# 08 §6.5 console entrypoint: adapter files are just `sys.exit(run(MyTeammate))`.
def run(teammate_cls: type[Teammate]) -> int:
    try:
        teammate = teammate_cls.from_argv()
        asyncio.run(teammate.main_loop())
        return 0
    except KeyboardInterrupt:
        return 130
