from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
import json
import os


class CloudStateError(RuntimeError):
    pass


class CloudStateLocked(CloudStateError):
    pass


class CloudStateConflict(CloudStateError):
    pass


class CloudStateStore:
    """Persist a single-writer SQLite job state as versioned GCS objects."""

    def __init__(
        self,
        bucket_name: str,
        prefix: str,
        database_path: str | Path,
        client: Any | None = None,
        now_fn: Any | None = None,
    ):
        if not bucket_name:
            raise ValueError("NEWSAGENT_STATE_BUCKET is required")
        self.bucket_name = bucket_name
        self.prefix = prefix.strip("/") or "newsagent"
        self.database_path = Path(database_path)
        self.client = client or self._default_client()
        self.bucket = self.client.bucket(bucket_name)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.state_generation = 0
        self.lock_generation: int | None = None

    @classmethod
    def from_environment(
        cls,
        database_path: str | Path,
        client: Any | None = None,
    ) -> "CloudStateStore":
        return cls(
            bucket_name=os.environ.get("NEWSAGENT_STATE_BUCKET", ""),
            prefix=os.environ.get("NEWSAGENT_STATE_PREFIX", "newsagent"),
            database_path=database_path,
            client=client,
        )

    @staticmethod
    def _default_client() -> Any:
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise RuntimeError("google-cloud-storage is required for cloud-daily") from exc
        return storage.Client()

    @property
    def state_object(self) -> str:
        return f"{self.prefix}/state/newsagent.db"

    @property
    def lock_object(self) -> str:
        return f"{self.prefix}/locks/daily.lock"

    @contextmanager
    def lease(self, owner: str, ttl_seconds: int = 4500) -> Iterator[None]:
        self.acquire_lock(owner, ttl_seconds=ttl_seconds)
        try:
            yield
        finally:
            self.release_lock()

    def acquire_lock(self, owner: str, ttl_seconds: int = 4500) -> int:
        if ttl_seconds <= 0:
            raise ValueError("lock TTL must be positive")
        now = self.now_fn()
        payload = json.dumps(
            {
                "owner": owner,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            },
            sort_keys=True,
        )

        for _attempt in range(2):
            blob = self.bucket.blob(self.lock_object)
            try:
                blob.upload_from_string(
                    payload,
                    content_type="application/json",
                    if_generation_match=0,
                )
                if not getattr(blob, "generation", None):
                    blob.reload()
                self.lock_generation = int(blob.generation)
                return self.lock_generation
            except Exception as exc:
                if not _is_precondition_failed(exc):
                    raise

            existing = self.bucket.blob(self.lock_object)
            try:
                existing.reload()
                generation = int(existing.generation)
                lock_data = json.loads(existing.download_as_text())
            except Exception as exc:
                if _is_not_found(exc):
                    continue
                raise CloudStateLocked("Cloud state lock exists and cannot be inspected") from exc

            expires_at = _parse_datetime(lock_data.get("expires_at"))
            if expires_at is None or expires_at > now:
                lock_owner = lock_data.get("owner", "unknown")
                raise CloudStateLocked(f"Cloud state is locked by {lock_owner}")
            try:
                existing.delete(if_generation_match=generation)
            except Exception as exc:
                if _is_not_found(exc) or _is_precondition_failed(exc):
                    continue
                raise

        raise CloudStateLocked("Cloud state lock changed while acquiring it")

    def release_lock(self) -> None:
        if self.lock_generation is None:
            return
        blob = self.bucket.blob(self.lock_object)
        try:
            blob.delete(if_generation_match=self.lock_generation)
        except Exception as exc:
            if not (_is_not_found(exc) or _is_precondition_failed(exc)):
                raise
        finally:
            self.lock_generation = None

    def restore_database(self) -> bool:
        blob = self.bucket.blob(self.state_object)
        try:
            blob.reload()
        except Exception as exc:
            if _is_not_found(exc):
                self.state_generation = 0
                for path in (
                    self.database_path,
                    Path(f"{self.database_path}-wal"),
                    Path(f"{self.database_path}-shm"),
                ):
                    path.unlink(missing_ok=True)
                return False
            raise
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(self.database_path))
        self.state_generation = int(blob.generation)
        return True

    def upload_database(self, snapshot_path: str | Path) -> int:
        blob = self.bucket.blob(self.state_object)
        try:
            blob.upload_from_filename(
                str(snapshot_path),
                content_type="application/vnd.sqlite3",
                if_generation_match=self.state_generation,
            )
        except Exception as exc:
            if _is_precondition_failed(exc):
                raise CloudStateConflict(
                    "Cloud database changed after it was restored; refusing to overwrite it"
                ) from exc
            raise
        if not getattr(blob, "generation", None):
            blob.reload()
        self.state_generation = int(blob.generation)
        return self.state_generation

    def upload_outbox(self, outbox: str | Path) -> int:
        root = Path(outbox)
        if not root.exists():
            return 0
        uploaded = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            blob = self.bucket.blob(f"{self.prefix}/outbox/{relative}")
            blob.upload_from_filename(str(path), content_type="text/markdown; charset=utf-8")
            uploaded += 1
        return uploaded


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _exception_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if callable(code):
        code = code()
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_not_found(exc: Exception) -> bool:
    return exc.__class__.__name__ == "NotFound" or _exception_code(exc) == 404


def _is_precondition_failed(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"PreconditionFailed", "Conflict"} or _exception_code(exc) in {409, 412}
