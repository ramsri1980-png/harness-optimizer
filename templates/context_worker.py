#!/usr/bin/env python3
"""CTX Day 5 — the MAP worker spawned by ``templates/auto-context.ts``.

A long-lived, newline-delimited JSON helper: one request line on stdin,
one reply line on stdout, forever.  The process exits when stdin closes
(EOF) or when it receives ``{"cmd": "shutdown"}``.

Protocol
--------
Request::

    {"cmd": "map", "root": "/abs/path", "max_tokens": 1500}
    {"cmd": "ping"}
    {"cmd": "shutdown"}

Reply::

    {"ok": true, "text": "<ranked map>", "bytes": 1234, "ms": 42}
    {"ok": true, "pong": true}
    {"ok": true}
    {"ok": false, "error": "..."}

Rules of the channel
--------------------
* stdout is the protocol channel and carries **only** reply lines.
  Every diagnostic, warning and traceback goes to stderr.
* Request lines are bounded at 64 KiB; reply text is capped at 64 KiB
  before it is serialized.
* One bad request never kills the process: it is answered with
  ``{"ok": false, ...}`` (or logged to stderr) and the loop continues.
* Every error path fails open — the caller (the plugin) simply injects
  nothing when the reply is not usable.

The ranked map itself comes from ``harness_core.repomap_adapter``, whose
in-process fingerprint cache (Day 3) makes repeat calls cheap, so the
plugin can afford to spawn-on-demand without a REUSE fast path.
"""

import json
import os
import sys
import time
import traceback

# Import harness_core from ../harness_core (relative to this file).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_IMPORT_ERROR = None
try:
    from harness_core import repomap_adapter
except Exception as exc:  # pragma: no cover - only on a broken install
    repomap_adapter = None
    _IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

MAX_REQUEST_BYTES = 64 * 1024
MAX_REPLY_BYTES = 64 * 1024
DEFAULT_MAX_TOKENS = 1500
MAX_FILES = 500
# Upper bound on the caller's max_tokens so a malformed request line can
# never ask the ranker for a multi-megabyte render.
MAX_TOKENS_CEILING = 8192


def log(message):
    """Diagnostic output — stderr only; stdout is the protocol channel."""
    try:
        sys.stderr.write("[context_worker] %s\n" % message)
        sys.stderr.flush()
    except Exception:
        pass


def send(payload):
    """Write one reply line to stdout. Returns False when the pipe is gone."""
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        return True
    except Exception as exc:
        log("stdout write failed: %r" % exc)
        return False


def cap_text(text):
    """Bound the reply payload at 64 KiB (UTF-8 aware, never splits a char)."""
    raw = text.encode("utf-8", "ignore")
    if len(raw) <= MAX_REPLY_BYTES:
        return text
    return raw[:MAX_REPLY_BYTES].decode("utf-8", "ignore")


def positive_int(value, default):
    try:
        number = int(value)
    except Exception:
        return default
    if number <= 0:
        return default
    return min(number, MAX_TOKENS_CEILING)


def handle_map(request):
    """Build a ranked repo map for ``request["root"]`` and reply with it."""
    if repomap_adapter is None:
        return send({"ok": False, "error": "harness_core import failed: %s" % _IMPORT_ERROR})

    root = request.get("root")
    if not isinstance(root, str) or not root.strip():
        return send({"ok": False, "error": "missing root"})

    max_tokens = positive_int(request.get("max_tokens"), DEFAULT_MAX_TOKENS)

    started = time.monotonic()
    text, raw_bytes = repomap_adapter.build_ranked_map_with_stats(
        root, max_tokens=max_tokens, max_files=MAX_FILES
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    # The adapter reports failures as strings rather than exceptions.
    if not isinstance(text, str):
        return send({"ok": False, "error": "adapter returned no text"})
    if text.startswith("ERROR:"):
        return send({"ok": False, "error": text.splitlines()[0][:200]})
    if not text.strip():
        return send({"ok": False, "error": "empty ranked map"})

    return send(
        {
            "ok": True,
            "text": cap_text(text),
            "bytes": int(raw_bytes or 0),
            "ms": elapsed_ms,
        }
    )


def dispatch(line):
    """Handle exactly one request line. Returns False to stop the loop."""
    if len(line) > MAX_REQUEST_BYTES:
        return send({"ok": False, "error": "request too large"})

    try:
        request = json.loads(line.decode("utf-8"))
    except Exception:
        return send({"ok": False, "error": "invalid json"})

    if not isinstance(request, dict):
        return send({"ok": False, "error": "invalid request"})

    cmd = request.get("cmd")
    if cmd == "ping":
        return send({"ok": True, "pong": True})
    if cmd == "shutdown":
        send({"ok": True})
        return False
    if cmd == "map":
        return handle_map(request)
    return send({"ok": False, "error": "unknown cmd"})


def main():
    log("started pid=%d" % os.getpid())
    while True:
        try:
            line = sys.stdin.buffer.readline()
        except Exception as exc:
            log("stdin read failed: %r" % exc)
            break
        if not line:
            # stdin EOF: the plugin (our parent) is gone. Exit quietly.
            log("stdin closed — exiting")
            break
        try:
            if not dispatch(line):
                break
        except Exception:
            # One bad request must not take down the whole worker.
            log("unhandled exception:\n%s" % traceback.format_exc())
            if not send({"ok": False, "error": "internal error"}):
                break
    log("bye")


if __name__ == "__main__":
    main()
