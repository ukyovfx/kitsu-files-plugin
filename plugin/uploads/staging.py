"""Server-owned persistent staging directory; callers can address only exact session keys."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID


_KEY = re.compile(r"^upload/[0-9a-fA-F-]{36}/[0-9]{1,8}$")


class FileSystemStaging:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError("Invalid server-generated staging key")
        result = (self.root / key).resolve()
        if self.root not in result.parents:
            raise ValueError("Staging key escaped configured root")
        return result

    def put(self, key: str, data: bytes, expected_sha256: str) -> None:
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("Staging write hash mismatch")
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temp:
                temp_name = temp.name
                temp.write(data)
                temp.flush()
                os.fsync(temp.fileno())
            try:
                os.link(temp_name, target)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha256:
                    raise ValueError("Staging key already contains different bytes")
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def open(self, key: str):
        return self._resolve(key).open("rb")

    def delete_exact(self, key: str) -> bool:
        target = self._resolve(key)
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        return True

    def delete_session_chunks(self, session_id: str, recorded_keys: list[str]) -> None:
        """Delete only recorded or numeric chunk files under one validated UUID session."""
        UUID(session_id)
        for key in recorded_keys:
            if not key.startswith(f"upload/{session_id}/"):
                raise ValueError("Cleanup key is outside the claimed upload session")
            self.delete_exact(key)
        session_dir = (self.root / "upload" / session_id).resolve()
        if self.root not in session_dir.parents:
            raise ValueError("Upload session escaped staging root")
        if not session_dir.exists():
            return
        for child in session_dir.iterdir():
            if child.is_file() and child.name.isdecimal():
                child.unlink()
        try:
            session_dir.rmdir()
        except OSError:
            pass
