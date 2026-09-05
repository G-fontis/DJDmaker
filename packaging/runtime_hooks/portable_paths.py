"""Make reviewed portable media tools discoverable without changing cwd."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    portable_root = Path(sys.executable).resolve().parent
    media_bin = portable_root / "runtime" / "ffmpeg"
    os.environ["PATH"] = os.pathsep.join(
        (str(media_bin), os.environ.get("PATH", ""))
    ).rstrip(os.pathsep)
