#!/usr/bin/env python3
"""CTX Day 1 smoke test.

End-to-end proof that ``templates/auto-context.ts`` injects a labeled
reference-data packet into the *outgoing* model request, and that the model
provider actually sees it.

Run directly::

    python3 tests/test_ctx_smoke.py

Deliberately NOT wired into ``tests/test_fixes.py``: it spawns OpenCode plus a
local stub provider and would slow the main suite.

Reuses the CTX spike's local OpenAI-compatible stub provider
(``/tmp/opencode/ctx_spike/stub_provider.py``). That stub appends every raw
request body it receives to ``/tmp/opencode/ctx_spike/request.jsonl``; this
test reads only the bytes appended after it starts, so existing spike
evidence is never disturbed. When the stub is absent the test skips (exit 0).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO_ROOT, "templates", "auto-context.ts")
FIXTURE_SRC = os.path.join(REPO_ROOT, "tests", "ctx_fixtures", "hello.txt")

STUB_PATH = "/tmp/opencode/ctx_spike/stub_provider.py"
STUB_OUT = "/tmp/opencode/ctx_spike/request.jsonl"
STUB_READY = "listening on 127.0.0.1:8123"

MARKER = "[HARNESS REPOSITORY CONTEXT — reference data]"
FIXTURE_SNIPPET = "Hello from the CTX fixture."
PROMPT = "Please look at ctx_fixtures/hello.txt and tell me what it says."
GEN_PROMPT = "hi, how are you today?"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SHA_LINE_RE = re.compile(r"^# sha256: ([0-9a-f]{64})$", re.MULTILINE)

BOOT_TIMEOUT = 30
RUN_TIMEOUT = 180

check_results = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    check_results.append((label, bool(condition)))
    return condition


def _message_texts(message):
    """Text of a wire message, tolerating the several shapes we may see.

    Inside the ``context`` hook a user message is
    ``content: [{type: "text", text}, ...]``. Once serialized to an
    OpenAI-compatible provider, user/assistant text collapses to a plain
    string and ``metadata`` is dropped. Both must be readable.
    """
    container = message.get("content")
    if isinstance(container, str):
        return [container]
    if not isinstance(container, list):
        container = message.get("parts")
    if not isinstance(container, list):
        return []
    texts = []
    for part in container:
        if isinstance(part, dict) and part.get("type") == "text":
            texts.append(str(part.get("text", "")))
    return texts


def _find_marker_message(messages):
    for index, message in enumerate(messages):
        for text in _message_texts(message):
            if MARKER in text:
                return index, message, text
    return None, None, None


def _find_prompt_index(messages):
    for index, message in enumerate(messages):
        for text in _message_texts(message):
            if PROMPT in text:
                return index
    return None


def _wait_for_stub(proc, log_path):
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with open(log_path, "rb") as fh:
                if STUB_READY.encode("utf-8") in fh.read():
                    return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def _stop_stub(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except Exception:
        pass


def _read_appended(offset):
    """Bodies the stub recorded after this test started it."""
    if not os.path.isfile(STUB_OUT):
        return [], b""
    with open(STUB_OUT, "rb") as fh:
        fh.seek(offset)
        raw = fh.read()
    bodies = []
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            bodies.append(json.loads(line.decode("utf-8")))
        except Exception:
            pass
    return bodies, raw


def _dump(path, limit=2000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            sys.stdout.write(fh.read(limit))
            sys.stdout.write("\n")
    except OSError as exc:
        sys.stdout.write("<unavailable: %s>\n" % exc)


def _run_assertions(bodies, raw):
    raw_text = raw.decode("utf-8", "replace")
    ok = True

    ok &= _check(
        "CTX-SMOKE-01: stub captured at least one request body",
        len(bodies) > 0,
        "captured=%d" % len(bodies),
    )
    ok &= _check(
        "CTX-SMOKE-02: request contains the HARNESS REPOSITORY CONTEXT marker",
        MARKER in raw_text,
    )
    ok &= _check(
        "CTX-SMOKE-03: request contains the fixture content",
        FIXTURE_SNIPPET in raw_text,
    )

    has_metadata = False
    placed_before_query = False
    packet_seen = False
    max_packets = 0
    for body in bodies:
        messages = body.get("messages")
        if not isinstance(messages, list):
            continue
        count = 0
        for index, message in enumerate(messages):
            for text in _message_texts(message):
                if MARKER in text:
                    count += 1
                    metadata = message.get("metadata")
                    if isinstance(metadata, dict) and metadata.get("harness_auto_context") is True:
                        has_metadata = True
        max_packets = max(max_packets, count)
        index, _message, _text = _find_marker_message(messages)
        if index is not None:
            packet_seen = True
            query_index = _find_prompt_index(messages)
            if query_index is not None and index < query_index:
                placed_before_query = True

    ok &= _check(
        "CTX-SMOKE-04: packet is a discrete text message in the request",
        packet_seen,
    )
    ok &= _check(
        "CTX-SMOKE-05: packet is harness-owned "
        "(metadata set, or positioned immediately before the user query)",
        has_metadata or placed_before_query,
        "metadata=%s positional=%s" % (has_metadata, placed_before_query),
    )
    ok &= _check(
        "CTX-SMOKE-06: no fabricated assistant acknowledgement in the request",
        "acknowledge" not in raw_text.lower(),
    )
    ok &= _check(
        "CTX-SMOKE-07: at most one packet per outgoing request (idempotent)",
        max_packets <= 1,
        "max packets in any single request=%d" % max_packets,
    )

    if not ok:
        print("\n----- captured request body (first 2 KB) -----")
        sys.stdout.write(raw_text[:2048] + "\n")
        print("----- end captured request body -----")

    return ok


def _primary_body(bodies):
    """The primary agent request: the largest captured body.

    Auxiliary requests (title generation) are far smaller than the primary
    loop request — see docs/ctx-spike-findings.md §6 (2446 vs 21162 bytes).
    """
    candidates = [b for b in bodies if isinstance(b, dict) and isinstance(b.get("messages"), list)]
    if not candidates:
        return None
    return max(candidates, key=lambda b: len(json.dumps(b)))


def _is_title_request(body):
    """A title request carries a system message mentioning the title generator."""
    for message in body.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        for text in _message_texts(message):
            if "title generator" in text.lower():
                return True
    return False


def _check_api01(bodies):
    """No title-generation request may carry the harness packet."""
    title_bodies = [b for b in bodies if isinstance(b, dict) and _is_title_request(b)]
    marker_free = all(MARKER not in json.dumps(b) for b in title_bodies)
    if title_bodies:
        detail = "title_requests=%d" % len(title_bodies)
    else:
        detail = "title_requests=0 (none observed)"
    return _check(
        "CTX-API-01: title request has no packet",
        marker_free,
        detail,
    )


def _check_api05(bodies):
    """The injected packet must carry a real (non-empty) sha256 fingerprint."""
    primary = _primary_body(bodies)
    digest = None
    if primary is not None:
        for message in primary.get("messages", []):
            for text in _message_texts(message):
                match = SHA_LINE_RE.search(text)
                if match:
                    digest = match.group(1)
                    break
            if digest:
                break
    ok = bool(digest) and digest != EMPTY_SHA256
    if digest:
        print("[packet] # sha256: %s" % digest)
    return _check(
        "CTX-API-05: packet has a real sha256 fingerprint",
        ok,
        "sha256=%s" % (digest or "<missing>"),
    )


def _check_api03(env, workspace):
    """A plain conversational prompt must produce no packet at all."""
    offset = os.path.getsize(STUB_OUT) if os.path.isfile(STUB_OUT) else 0
    proc = subprocess.run(
        ["opencode", "run", "--standalone", "--auto", "-m", "stub/stub-1", GEN_PROMPT],
        cwd=workspace,
        env=env,
        capture_output=True,
        timeout=RUN_TIMEOUT,
    )
    print("[opencode] general-conversation run exit=%s" % proc.returncode)

    bodies, raw = _read_appended(offset)
    raw_text = raw.decode("utf-8", "replace")
    print("[stub] captured %d request body/bodies from the general-conversation run" % len(bodies))

    primary = _primary_body(bodies)
    marker_present = MARKER in raw_text
    source_line_present = (
        "# source:" in json.dumps(primary) if primary is not None else None
    )
    ok = len(bodies) > 0 and not marker_present and source_line_present is False
    return _check(
        "CTX-API-03: general conversation produces no packet",
        ok,
        "captured=%d marker=%s source_line=%s"
        % (len(bodies), marker_present, source_line_present),
    )


def main():
    for label, path in (
        ("stub provider", STUB_PATH),
        ("plugin", PLUGIN),
        ("fixture", FIXTURE_SRC),
    ):
        if not os.path.isfile(path):
            if label == "stub provider":
                print("SKIP: %s not present at %s — CTX smoke test skipped." % (label, path))
                return 0
            print("FAIL: %s not found at %s" % (label, path))
            return 1

    with open(FIXTURE_SRC, "r", encoding="utf-8") as fh:
        fixture_content = fh.read()
    if FIXTURE_SNIPPET not in fixture_content:
        print("FAIL: fixture %s lacks the expected snippet %r" % (FIXTURE_SRC, FIXTURE_SNIPPET))
        return 1

    # The stub appends; remember where our run starts so we never disturb
    # the spike's existing capture.
    offset = os.path.getsize(STUB_OUT) if os.path.isfile(STUB_OUT) else 0

    tmp = tempfile.mkdtemp(prefix="ctx-smoke-")
    stub = None
    try:
        stub_log_path = os.path.join(tmp, "stub.log")
        with open(stub_log_path, "wb") as stub_log:
            stub = subprocess.Popen(
                [sys.executable, STUB_PATH],
                stdout=subprocess.DEVNULL,
                stderr=stub_log,
            )

        if not _wait_for_stub(stub, stub_log_path):
            print("FAIL: stub provider did not report ready within %ds" % BOOT_TIMEOUT)
            print("----- stub log -----")
            _dump(stub_log_path)
            return 1

        config_dir = os.path.join(tmp, "config")
        home_dir = os.path.join(tmp, "home")
        workspace = os.path.join(tmp, "workspace")
        os.makedirs(config_dir)
        os.makedirs(home_dir)
        os.makedirs(os.path.join(workspace, "ctx_fixtures"))
        shutil.copyfile(
            FIXTURE_SRC, os.path.join(workspace, "ctx_fixtures", "hello.txt")
        )

        # OpenCode v2.0.24 rejects a bare file in `plugins`
        # ("configured plugin path must be a directory"), so hand it a
        # plugin directory holding a copy of the plugin under test.
        plugin_dir = os.path.join(config_dir, "plugins")
        os.makedirs(plugin_dir)
        shutil.copyfile(PLUGIN, os.path.join(plugin_dir, "auto-context.ts"))

        config = {
            "$schema": "https://opencode.ai/config.json",
            "providers": {
                "stub": {
                    "package": "@opencode/ai/providers/openai-compatible",
                    "settings": {"baseURL": "http://127.0.0.1:8123/v1"},
                    "models": {"stub-1": {"name": "Stub"}},
                }
            },
            "agents": {"primary": {"model": "stub/stub-1"}},
            "plugins": [plugin_dir],
        }
        with open(os.path.join(config_dir, "opencode.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)

        # Build a minimal environment rather than inheriting the caller's.
        # An ambient OpenCode session (e.g. `OPENCODE_SESSION_ID` exported by
        # an agent harness) makes the child attach to that session's project,
        # so `ctx.location.directory` points at the *caller's* repo instead of
        # this test's workspace and the packet is built from the wrong root.
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", os.path.expanduser("~")),
            "OPENCODE_CONFIG_DIR": config_dir,
            "OPENCODE_TEST_HOME": home_dir,
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
        }

        print("\n[opencode] running standalone against the stub provider ...")
        proc = subprocess.run(
            ["opencode", "run", "--standalone", "--auto", "-m", "stub/stub-1", PROMPT],
            cwd=workspace,
            env=env,
            capture_output=True,
            timeout=RUN_TIMEOUT,
        )
        print("[opencode] exit=%s" % proc.returncode)
        print("----- opencode stdout (first 2 KB) -----")
        sys.stdout.write(proc.stdout.decode("utf-8", "replace")[:2048])
        print("----- opencode stderr (first 2 KB) -----")
        sys.stdout.write(proc.stderr.decode("utf-8", "replace")[:2048])
        print("----- end opencode output -----")

        bodies, raw = _read_appended(offset)
        print("\n[stub] captured %d request body/bodies from %s" % (len(bodies), STUB_OUT))

        ok = _run_assertions(bodies, raw)

        # CTX Day 2 checks, reusing the same stub and the same captured run.
        ok &= _check_api01(bodies)
        ok &= _check_api05(bodies)
        ok &= _check_api03(env, workspace)

        print("\n%d/%d checks passed" % (sum(1 for _, c in check_results if c), len(check_results)))
        return 0 if ok else 1

    except subprocess.TimeoutExpired:
        print("FAIL: `opencode run` exceeded %ds and was aborted" % RUN_TIMEOUT)
        return 1
    finally:
        _stop_stub(stub)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
