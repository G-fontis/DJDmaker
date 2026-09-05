from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


class JsonStore:
    """UTF-8 JSONを同一directory内の一時ファイル経由で原子的に置換する。"""

    def __init__(
        self,
        path: str | Path,
        *,
        replace_retry_delays: tuple[float, ...] = (),
    ) -> None:
        self.path = Path(path)
        self.replace_retry_delays = replace_retry_delays

    def load(self, default: Any = None) -> Any:
        if not self.path.exists():
            return default
        with self.path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def save(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        ready_to_publish = False
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            ready_to_publish = True
            self._replace(temporary, self.path)
        finally:
            # A fully flushed temporary is crash/retry recovery evidence when
            # the final publish remains blocked. Partial JSON is never kept.
            if not ready_to_publish:
                temporary.unlink(missing_ok=True)

    def _replace(self, source: Path, destination: Path) -> None:
        for delay in (*self.replace_retry_delays, None):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if delay is None:
                    raise
                time.sleep(delay)
