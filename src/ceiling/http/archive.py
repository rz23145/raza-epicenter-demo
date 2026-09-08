"""Raw response archive.

Every fetched body is written to data/raw/{host}/{yyyy-mm-dd}/{sha256}.html
with a sidecar {sha256}.json carrying url, status, headers, and fetched_at,
so every stored measurement can be traced back to bytes on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ceiling.util import sha256_hex


class RawArchive:
    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir

    def store(
        self,
        host: str,
        scan_date: str,
        body: bytes,
        meta: dict[str, Any],
    ) -> tuple[str, Path]:
        """Write body and sidecar, returning (sha256, body_path). Idempotent."""
        sha = sha256_hex(body)
        directory = self.raw_dir / host / scan_date
        directory.mkdir(parents=True, exist_ok=True)
        body_path = directory / f"{sha}.html"
        if not body_path.exists():
            body_path.write_bytes(body)
        sidecar = directory / f"{sha}.json"
        meta = dict(meta)
        meta["sha256"] = sha
        sidecar.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        return sha, body_path

    @staticmethod
    def load(body_path: str | Path) -> bytes:
        return Path(body_path).read_bytes()
