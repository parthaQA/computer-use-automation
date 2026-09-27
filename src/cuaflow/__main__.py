"""Same entry as python -m cuaflow.discover."""

from __future__ import annotations

import asyncio
import sys

from cuaflow.discover.__main__ import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
