"""Bounded, worktree-scoped JSON cache — CORE (Day 2).

A single JSON document on disk::

    {"version": 1, "buckets": {worktree: {key: value}}}

Values are validated before they are stored (JSON-compatible types
only, per-entry size cap, per-string length cap), writes are atomic
(temp file in the same directory → ``fsync`` → ``os.replace``), and
both a per-worktree and a whole-file budget are enforced by silently
dropping the least-recently-set entries. A corrupt, truncated or
wrong-version file is treated as an empty cache rather than an error.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading

CACHE_VERSION = 1
DEFAULT_PER_WORKTREE_BYTES = 4 * 1024 * 1024
DEFAULT_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ENTRY_BYTES = 32 * 1024
MAX_STRING_LENGTH = 8 * 1024

#: Guard against cyclic / pathologically nested values during validation.
_MAX_VALUE_DEPTH = 32


class CacheError(Exception):
    """Base class for cache I/O failures."""


class EntryTooLarge(CacheError):
    """Value exceeds the per-entry size or per-string length cap."""


class UnsupportedValue(CacheError):
    """Value is not JSON-compatible (or has non-string mapping keys)."""


class Cache:
    """Thread-safe, size-bounded cache keyed by ``(worktree, key)``."""

    def __init__(self, path,
                 per_worktree_bytes=DEFAULT_PER_WORKTREE_BYTES,
                 total_bytes=DEFAULT_TOTAL_BYTES):
        self.path = os.fspath(path)
        self.per_worktree_bytes = per_worktree_bytes
        self.total_bytes = total_bytes
        self._lock = threading.Lock()
        self._buckets = {}
        # In-memory only: worktree -> key -> set order. Never persisted.
        self._last_set = {}
        self._seq = 0
        self._load()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def get(self, worktree, key):
        """Return the stored value, or ``None`` when absent.

        Refreshes the key's set order so recently-read entries are the
        last candidates for eviction.
        """
        with self._lock:
            bucket = self._buckets.get(worktree)
            if bucket is None or key not in bucket:
                return None
            self._stamp(worktree, key)
            return bucket[key]

    def set(self, worktree, key, value):
        """Store *value* and re-save atomically, then enforce budgets.

        Raises UnsupportedValue for non-JSON-compatible values and
        EntryTooLarge when the value exceeds ``MAX_ENTRY_BYTES`` or
        contains a string longer than ``MAX_STRING_LENGTH``.
        """
        _validate_value(value)
        payload = json.dumps(value)
        if len(payload.encode("utf-8")) > MAX_ENTRY_BYTES:
            raise EntryTooLarge(
                f"entry is {len(payload.encode('utf-8'))} bytes, "
                f"limit is {MAX_ENTRY_BYTES}")
        for text in _iter_strings(value):
            if len(text) > MAX_STRING_LENGTH:
                raise EntryTooLarge(
                    f"string of {len(text)} chars exceeds "
                    f"limit {MAX_STRING_LENGTH}")

        with self._lock:
            # Store the JSON round-trip, not the caller's object: the
            # in-memory value then always equals what is on disk, so a
            # later mutation by the caller cannot drift the budgets.
            self._bucket(worktree)[key] = json.loads(payload)
            self._stamp(worktree, key)
            self._save()
            if self._evict():
                self._save()

    def invalidate(self, worktree, key=None):
        """Drop one key, or the whole bucket when *key* is omitted."""
        with self._lock:
            if key is None:
                self._buckets.pop(worktree, None)
                self._last_set.pop(worktree, None)
            else:
                bucket = self._buckets.get(worktree)
                if bucket is not None and key in bucket:
                    del bucket[key]
                    times = self._last_set.get(worktree)
                    if times is not None:
                        times.pop(key, None)
                    if not bucket:
                        self._buckets.pop(worktree, None)
                        self._last_set.pop(worktree, None)
            self._save()

    def clear(self):
        """Reset to an empty cache document and save."""
        with self._lock:
            self._buckets = {}
            self._last_set = {}
            self._seq = 0
            self._save()

    def stats(self):
        """Return size/count counters for the current document."""
        with self._lock:
            doc = {"version": CACHE_VERSION, "buckets": self._buckets}
            return {
                "bytes": len(json.dumps(doc).encode("utf-8")),
                "worktrees": len(self._buckets),
                "keys": sum(len(b) for b in self._buckets.values()),
                "per_worktree_limit": self.per_worktree_bytes,
                "total_limit": self.total_bytes,
            }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _load(self):
        """Read the document; a missing/corrupt one means an empty cache."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CacheError(
                f"cannot read cache file {self.path}: {exc}") from exc
        except ValueError:
            return  # undecodable content — treat as empty
        try:
            doc = json.loads(raw)
        except ValueError:
            return  # corrupt / truncated JSON — treat as empty
        if not isinstance(doc, dict) or doc.get("version") != CACHE_VERSION:
            return
        buckets = doc.get("buckets")
        if not isinstance(buckets, dict):
            return
        for worktree, bucket in buckets.items():
            if not isinstance(worktree, str) or not isinstance(bucket, dict):
                continue
            stored = {k: v for k, v in bucket.items()
                      if isinstance(k, str)}
            if not stored:
                continue
            self._buckets[worktree] = stored
            self._last_set[worktree] = {}
            for key in stored:  # file order becomes eviction order
                self._stamp(worktree, key)

    def _save(self):
        """Write the document atomically: temp file, fsync, replace."""
        doc = {"version": CACHE_VERSION, "buckets": self._buckets}
        payload = json.dumps(doc)
        directory = os.path.dirname(os.path.abspath(self.path))
        tmp_path = None
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".cache-",
                                            suffix=".tmp",
                                            dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
            tmp_path = None
            _fsync_dir(directory)
        except OSError as exc:
            raise CacheError(
                f"cannot write cache file {self.path}: {exc}") from exc
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _bucket(self, worktree):
        """Return (creating on demand) the bucket for *worktree*."""
        times = self._last_set.get(worktree)
        if times is None:
            self._last_set[worktree] = {}
        bucket = self._buckets.get(worktree)
        if bucket is None:
            bucket = {}
            self._buckets[worktree] = bucket
        return bucket

    def _stamp(self, worktree, key):
        """Record *key* as the most recently set entry of its bucket."""
        self._seq += 1
        self._last_set.setdefault(worktree, {})[key] = self._seq

    def _oldest_key(self, worktree=None):
        """Return ``(bucket_worktree, key)`` of the least-recently-set
        entry, considering one bucket or the whole cache."""
        oldest = None
        oldest_seq = None
        for wt, bucket in self._buckets.items():
            if worktree is not None and wt != worktree:
                continue
            times = self._last_set.get(wt, {})
            for key in bucket:
                stamp = times.get(key, 0)
                if oldest_seq is None or stamp < oldest_seq:
                    oldest_seq = stamp
                    oldest = (wt, key)
        return oldest

    def _drop(self, victim):
        """Remove one ``(worktree, key)`` pair, pruning empty buckets."""
        worktree, key = victim
        bucket = self._buckets.get(worktree)
        if bucket is None or key not in bucket:
            return
        del bucket[key]
        times = self._last_set.get(worktree)
        if times is not None:
            times.pop(key, None)
        if not bucket:
            self._buckets.pop(worktree, None)
            self._last_set.pop(worktree, None)

    def _evict(self):
        """Enforce both budgets by dropping oldest entries.

        Never raises: worst case the cache ends up empty. Returns True
        when anything was dropped (so the caller can persist).
        """
        changed = False
        try:
            for worktree in list(self._buckets):
                bucket = self._buckets.get(worktree)
                if bucket is None:
                    continue
                while bucket and len(json.dumps(
                        bucket).encode("utf-8")) > self.per_worktree_bytes:
                    victim = self._oldest_key(worktree)
                    if victim is None:
                        break
                    self._drop(victim)
                    changed = True
            while True:
                doc = {"version": CACHE_VERSION, "buckets": self._buckets}
                if len(json.dumps(doc).encode("utf-8")) <= self.total_bytes:
                    break
                victim = self._oldest_key()
                if victim is None:
                    break
                self._drop(victim)
                changed = True
        except Exception:
            # Eviction must never raise; drop nothing further.
            return changed
        return changed


def _validate_value(value, depth=0):
    """Raise UnsupportedValue unless *value* is JSON-compatible."""
    if depth > _MAX_VALUE_DEPTH:
        raise UnsupportedValue("value nested too deeply (or is cyclic)")
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedValue("NaN and Infinity are not JSON-compatible")
        return
    if isinstance(value, list):
        for item in value:
            _validate_value(item, depth + 1)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise UnsupportedValue(
                    f"mapping keys must be str, got {type(k).__name__}")
            _validate_value(v, depth + 1)
        return
    raise UnsupportedValue(f"value of type {type(value).__name__} "
                           "is not JSON-compatible")


def _iter_strings(value):
    """Yield every string contained anywhere inside *value*."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _iter_strings(v)


def _fsync_dir(directory):
    """Best-effort durability for the rename itself — never raises."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
