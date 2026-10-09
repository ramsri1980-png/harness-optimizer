"""Q3 filesystem access policy — approved-workspace-only reads.

Reference: docs/ctx-decisions.md (Q3), docs/ctx-spike-findings.md section 5.

Every read goes through :meth:`Workspace.resolve`, which canonicalizes the
path (``~`` expansion + ``os.path.realpath``), requires it to be an existing
regular file inside an approved root, and rejects sensitive patterns.
"""
from __future__ import annotations

import hashlib
import os
from fnmatch import fnmatch
from typing import NamedTuple

DEFAULT_SENSITIVE_PATTERNS = (
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa",
    "**/id_ed25519",
    "**/credentials",
    "**/credentials.json",
    "**/.aws/credentials",
    "**/.ssh/id_*",
    "**/.git/config",
)

#: Streaming chunk size used when hashing file content (64 KiB).
HASH_CHUNK_SIZE = 64 * 1024


class WorkspaceError(Exception):
    """Base class for workspace authorization/policy failures."""


class PathNotAuthorized(WorkspaceError):
    """Canonical path is outside every approved root."""


class PathExcluded(WorkspaceError):
    """Canonical path matches a sensitive-file exclusion pattern."""


class MissingFile(WorkspaceError):
    """Canonical path does not exist or is not a regular file."""


class ReadTooLarge(WorkspaceError):
    """File exceeds the configured read limit."""


class SnapshotMismatch(WorkspaceError):
    """A previously captured snapshot no longer matches the file."""


class Snapshot(NamedTuple):
    path: str
    size: int
    sha256: str
    mtime_ns: int


class Workspace:
    """Authorization boundary for source reads (Q3)."""

    def __init__(self, roots, extra_exclusions=(), max_read_bytes=1_000_000):
        if roots is None:
            roots = ()
        elif isinstance(roots, (str, bytes, os.PathLike)):
            roots = (roots,)
        else:
            roots = tuple(roots)
        if not roots:
            raise WorkspaceError("at least one approved workspace root is required")

        canonical_roots = []
        for root in roots:
            canonical = os.path.realpath(os.path.expanduser(os.fspath(root)))
            if not os.path.isdir(canonical):
                raise WorkspaceError(f"root is not an existing directory: {root}")
            canonical_roots.append(canonical)

        self._roots = tuple(canonical_roots)
        self.exclusions = tuple(DEFAULT_SENSITIVE_PATTERNS) + tuple(extra_exclusions)
        self.max_read_bytes = max_read_bytes

    @property
    def roots(self):
        """Tuple of canonical absolute approved root directories."""
        return self._roots

    def resolve(self, path):
        """Return the canonical absolute path, or raise a WorkspaceError.

        Raises MissingFile when the path is not an existing regular file,
        PathNotAuthorized when it is outside every approved root, and
        PathExcluded when it matches an exclusion pattern.
        """
        canonical = os.path.realpath(os.path.expanduser(os.fspath(path)))
        if not os.path.isfile(canonical):
            raise MissingFile(str(path))

        root = self._matching_root(canonical)
        if root is None:
            raise PathNotAuthorized(str(path))

        relative = os.path.relpath(canonical, root)
        for pattern in self.exclusions:
            # All stock patterns start with "**/", which fnmatch translates
            # to a regex requiring a literal "/". A file at the workspace
            # root has no "/" in its relpath, so also test the "/"-prefixed
            # form, which matches root-level files without over-matching.
            if fnmatch(relative, pattern) or fnmatch("/" + relative, pattern):
                raise PathExcluded(str(path))

        return canonical

    def snapshot(self, path):
        """Capture size, sha256 and mtime_ns of the authorized file."""
        canonical = self.resolve(path)
        size = os.path.getsize(canonical)
        mtime_ns = os.stat(canonical).st_mtime_ns
        digest = hashlib.sha256()
        with open(canonical, "rb") as fh:
            for chunk in iter(lambda: fh.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        return Snapshot(
            path=canonical,
            size=size,
            sha256=digest.hexdigest(),
            mtime_ns=mtime_ns,
        )

    def read_bytes(self, path):
        """Return the whole file content, bounded by max_read_bytes."""
        canonical = self.resolve(path)
        size = os.path.getsize(canonical)
        if size > self.max_read_bytes:
            raise ReadTooLarge(
                f"{canonical}: {size} bytes exceeds limit {self.max_read_bytes}"
            )
        with open(canonical, "rb") as fh:
            data = fh.read()
        if len(data) > self.max_read_bytes:
            raise ReadTooLarge(
                f"{canonical}: {len(data)} bytes exceeds limit {self.max_read_bytes}"
            )
        return data

    def read_lines(self, path, start, end):
        """Read a 1-based inclusive line range plus a fresh snapshot."""
        if start < 1 or end < start:
            raise ValueError(f"invalid line range: start={start}, end={end}")
        data = self.read_bytes(path)
        lines = data.decode("utf-8", errors="replace").splitlines()
        selected = lines[start - 1:end]
        return selected, self.snapshot(path)

    def verify_unchanged(self, snapshot):
        """Re-read the file and raise SnapshotMismatch if anything changed."""
        current = self.snapshot(snapshot.path)
        if current.sha256 != snapshot.sha256:
            raise SnapshotMismatch("content hash changed")
        if current.mtime_ns != snapshot.mtime_ns:
            raise SnapshotMismatch("mtime changed")
        if current.size != snapshot.size:
            raise SnapshotMismatch(
                f"size changed: {snapshot.size} -> {current.size}"
            )

    def _matching_root(self, canonical):
        """Return the first approved root containing canonical, else None."""
        for root in self._roots:
            try:
                if os.path.commonpath([root, canonical]) == root:
                    return root
            except ValueError:
                # e.g. different drives / mixed absolute and relative paths
                continue
        return None
