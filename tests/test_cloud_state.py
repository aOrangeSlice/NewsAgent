from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
import json
import uuid

from newsagent.cloud_state import CloudStateConflict, CloudStateLocked, CloudStateStore


class NotFound(Exception):
    pass


class PreconditionFailed(Exception):
    pass


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name
        self.generation = None

    def _record(self):
        if self.name not in self.bucket.objects:
            raise NotFound(self.name)
        return self.bucket.objects[self.name]

    def reload(self):
        self.generation = self._record()["generation"]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        self._upload(str(data).encode("utf-8"), if_generation_match)

    def upload_from_filename(self, filename, content_type=None, if_generation_match=None):
        self._upload(Path(filename).read_bytes(), if_generation_match)

    def _upload(self, data, if_generation_match):
        current = self.bucket.objects.get(self.name)
        current_generation = current["generation"] if current else 0
        if if_generation_match is not None and if_generation_match != current_generation:
            raise PreconditionFailed(self.name)
        self.bucket.counter += 1
        self.generation = self.bucket.counter
        self.bucket.objects[self.name] = {
            "generation": self.generation,
            "data": data,
        }

    def download_as_text(self):
        return self._record()["data"].decode("utf-8")

    def download_to_filename(self, filename):
        Path(filename).write_bytes(self._record()["data"])

    def delete(self, if_generation_match=None):
        record = self._record()
        if if_generation_match is not None and if_generation_match != record["generation"]:
            raise PreconditionFailed(self.name)
        del self.bucket.objects[self.name]


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.counter = 0

    def blob(self, name):
        return FakeBlob(self, name)


class FakeClient:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, _name):
        return self._bucket


class CloudStateTests(TestCase):
    def make_path(self, name):
        return Path(__file__).resolve().parent / f"{name}_{uuid.uuid4().hex}.tmp"

    def make_store(self, bucket, path, now=None):
        return CloudStateStore(
            "test-bucket",
            "newsagent",
            path,
            client=FakeClient(bucket),
            now_fn=(lambda: now) if now else None,
        )

    def test_empty_state_upload_and_restore_round_trip(self) -> None:
        first_path = self.make_path("cloud_first")
        restored_path = self.make_path("cloud_restored")
        try:
            bucket = FakeBucket()
            first_path.write_bytes(b"sqlite snapshot")
            first = self.make_store(bucket, first_path)
            self.assertFalse(first.restore_database())
            self.assertFalse(first_path.exists())
            first_path.write_bytes(b"sqlite snapshot")
            generation = first.upload_database(first_path)
            self.assertGreater(generation, 0)

            second = self.make_store(bucket, restored_path)
            self.assertTrue(second.restore_database())
            self.assertEqual(restored_path.read_bytes(), b"sqlite snapshot")
        finally:
            first_path.unlink(missing_ok=True)
            restored_path.unlink(missing_ok=True)

    def test_database_generation_conflict_is_rejected(self) -> None:
        path = self.make_path("cloud_conflict")
        try:
            bucket = FakeBucket()
            path.write_bytes(b"one")
            store = self.make_store(bucket, path)
            store.upload_database(path)
            store.restore_database()

            external = bucket.blob(store.state_object)
            external.upload_from_string(b"external", if_generation_match=store.state_generation)
            path.write_bytes(b"two")
            with self.assertRaises(CloudStateConflict):
                store.upload_database(path)
        finally:
            path.unlink(missing_ok=True)

    def test_live_lock_blocks_second_owner(self) -> None:
        bucket = FakeBucket()
        first = self.make_store(bucket, self.make_path("cloud_one"))
        second = self.make_store(bucket, self.make_path("cloud_two"))
        first.acquire_lock("first")
        with self.assertRaisesRegex(CloudStateLocked, "first"):
            second.acquire_lock("second")
        first.release_lock()
        second.acquire_lock("second")
        second.release_lock()

    def test_expired_lock_is_replaced(self) -> None:
        now = datetime(2026, 8, 4, tzinfo=timezone.utc)
        bucket = FakeBucket()
        store = self.make_store(bucket, self.make_path("cloud_stale"), now=now)
        stale = bucket.blob(store.lock_object)
        stale.upload_from_string(
            json.dumps(
                {
                    "owner": "stale",
                    "expires_at": (now - timedelta(minutes=1)).isoformat(),
                }
            ),
            if_generation_match=0,
        )
        store.acquire_lock("new")
        lock_data = json.loads(bucket.blob(store.lock_object).download_as_text())
        self.assertEqual(lock_data["owner"], "new")
        store.release_lock()

    def test_lease_releases_lock_after_failure(self) -> None:
        bucket = FakeBucket()
        store = self.make_store(bucket, self.make_path("cloud_lease"))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with store.lease("owner"):
                raise RuntimeError("boom")
        self.assertNotIn(store.lock_object, bucket.objects)
