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
LIB_FIXTURE_SRC = os.path.join(REPO_ROOT, "tests", "ctx_fixtures", "lib", "util.py")
MAP_FIXTURE_SRC = os.path.join(REPO_ROOT, "tests", "ctx_fixtures", "map_fixture.py")
WORKER_SRC = os.path.join(REPO_ROOT, "templates", "context_worker.py")
HARNESS_CORE_SRC = os.path.join(REPO_ROOT, "templates", "harness_core")

STUB_PATH = "/tmp/opencode/ctx_spike/stub_provider.py"
STUB_OUT = "/tmp/opencode/ctx_spike/request.jsonl"
STUB_READY = "listening on 127.0.0.1:8123"

MARKER = "[HARNESS REPOSITORY CONTEXT — reference data]"
FOOTER = "[/HARNESS REPOSITORY CONTEXT]"
FIXTURE_SNIPPET = "Hello from the CTX fixture."
PROMPT = "Please look at ctx_fixtures/hello.txt and tell me what it says."
GEN_PROMPT = "hi, how are you today?"
MISSING_PROMPT = "Please look at ctx_fixtures/nonexistent.py and tell me what it says."
LIB_PROMPT = "Please look at ctx_fixtures/lib/util.py and tell me what it says."
MAP_PROMPT = "How is this repository structured? Give me a brief overview."
# CTX-12: a self-contained snippet — it has a `def` and a `print` but names
# no file, so it must not trigger a repo lookup.
SNIPPET_PROMPT = (
    "Given this snippet, what does it print?\n"
    "def f(x): return x * 2\n"
    "print(f(21))"
)
# CTX-16: generous ceiling on the ranked-map packet in UTF-8 bytes.
# 1500 tokens x ~4 chars/token = ~6000 chars, well under 8 KiB.
MAP_PACKET_MAX_BYTES = 8 * 1024
# Ranked-map entries render as `<relpath>:L<n> <kind> <name>`; the brief's
# "file:L<n> def <name>" means the file path may carry directories/extension.
MAP_DEF_LINE_RE = re.compile(r"\S+:L\d+ def \w+")
WORKER_PROC = "context_worker.py"
# Second language for the MAP workspace (see main()); Python alone would
# not produce the ranked map's `# languages:` header line.
MAP_JS_FIXTURE = """function summarize(entries) {
  return entries.length;
}

function rankEntries(entries) {
  return entries.slice().sort();
}
"""
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SHA_LINE_RE = re.compile(r"^# sha256: ([0-9a-f]{64})$", re.MULTILINE)
SOURCE_LINE_RE = re.compile(r"^# source: (.+)$", re.MULTILINE)

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


def _run_prompt(env, workspace, prompt, label):
    """One isolated opencode run turn; returns what the stub captured for it."""
    offset = os.path.getsize(STUB_OUT) if os.path.isfile(STUB_OUT) else 0
    proc = subprocess.run(
        ["opencode", "run", "--standalone", "--auto", "-m", "stub/stub-1", prompt],
        cwd=workspace,
        env=env,
        capture_output=True,
        timeout=RUN_TIMEOUT,
    )
    print("[opencode] %s run exit=%s" % (label, proc.returncode))

    bodies, raw = _read_appended(offset)
    print("[stub] captured %d request body/bodies from the %s run" % (len(bodies), label))
    return bodies, raw


def _count_workers():
    """Return the number of live context_worker.py processes."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", WORKER_PROC],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return len([line for line in out.stdout.splitlines() if line.strip()])
    except Exception:
        return 0


def _body_text(body):
    """Every text part of a captured body, decoded (no JSON escaping)."""
    if not isinstance(body, dict):
        return ""
    texts = []
    for message in body.get("messages", []):
        if not isinstance(message, dict):
            continue
        texts.extend(_message_texts(message))
    return "\n".join(texts)


def _primary_packet(primary):
    """(source, sha256) of the first harness packet in a captured body."""
    text = _body_text(primary)
    if MARKER not in text:
        return None, None
    source = SOURCE_LINE_RE.search(text)
    digest = SHA_LINE_RE.search(text)
    return (
        source.group(1).strip() if source else None,
        digest.group(1) if digest else None,
    )


def _packet_count(primary):
    """Number of harness-owned packet messages in a captured body."""
    if not isinstance(primary, dict):
        return 0
    return sum(
        1
        for message in primary.get("messages", [])
        if isinstance(message, dict)
        and any(MARKER in text for text in _message_texts(message))
    )


def _packet_text(primary):
    """Text between HEADER and FOOTER of the first packet in a body."""
    text = _body_text(primary)
    start = text.find(MARKER)
    if start < 0:
        return None
    end = text.find(FOOTER, start)
    if end < 0:
        return None
    return text[start:end]


def _check_api06(env, workspace, first_source, first_digest):
    """Second turn naming the SAME unchanged file: reuse, never duplicate."""
    bodies, _raw = _run_prompt(env, workspace, PROMPT, "second-mention")
    primary = _primary_body(bodies)
    packets = _packet_count(primary)
    source, digest = _primary_packet(primary)
    ok = (
        primary is not None
        and packets == 1
        and source is not None
        and source == first_source
        and digest is not None
        and digest == first_digest
    )
    return _check(
        "CTX-API-06: second mention reuses the packet "
        "(exactly one, same source, same sha256)",
        ok,
        "packets=%d source=%s sha256=%s (first run: source=%s sha256=%s)"
        % (packets, source, digest, first_source, first_digest),
    )


def _check_api08(env, workspace):
    """Fail open: a missing file must neither block nor inject."""
    bodies, raw = _run_prompt(env, workspace, MISSING_PROMPT, "missing-file")
    primary = _primary_body(bodies)
    packets = _packet_count(primary)
    marker_in_raw = MARKER in raw.decode("utf-8", "replace")
    ok = len(bodies) > 0 and primary is not None and packets == 0 and not marker_in_raw
    return _check(
        "CTX-API-08: missing file produces no packet and no failure",
        ok,
        "captured=%d packets=%d marker=%s" % (len(bodies), packets, marker_in_raw),
    )


def _check_api09(env, workspace):
    """Nested path support: ctx_fixtures/lib/util.py is injected, digest + body."""
    bodies, _raw = _run_prompt(env, workspace, LIB_PROMPT, "lib-fixture")
    primary = _primary_body(bodies)
    text = _body_text(primary)
    digest = SHA_LINE_RE.search(text)
    ok = (
        primary is not None
        and MARKER in text
        and "# source: ctx_fixtures/lib/util.py" in text
        and digest is not None
        and "def helper" in text
    )
    return _check(
        "CTX-API-09: nested lib fixture is injected with a sha256 fingerprint",
        ok,
        "marker=%s sha256=%s content=%s"
        % (MARKER in text, digest.group(1) if digest else "<missing>", "def helper" in text),
    )
def _check_api04(env, workspace):
    """CTX-API-04: a repo question naming no file injects the ranked map."""
    bodies, _raw = _run_prompt(env, workspace, MAP_PROMPT, "map-repo-question")
    primary = _primary_body(bodies)
    text = _body_text(primary)
    marker = MARKER in text
    source = "# source: <ranked-map>" in text
    header = "# ranked source map" in text
    languages = "# languages:" in text
    file_defs = MAP_DEF_LINE_RE.findall(text)
    ok = (
        primary is not None
        and marker
        and source
        and header
        and languages
        and len(file_defs) >= 3
    )
    return _check(
        "CTX-API-04: repo question injects the ranked map (no tool call)",
        ok,
        "captured=%d marker=%s source=%s header=%s languages=%s defs=%d"
        % (len(bodies), marker, source, header, languages, len(file_defs)),
    )


def _worker_pids():
    """PIDs of any running ``python3 .../context_worker.py`` processes."""
    proc = subprocess.run(["pgrep", "-f", WORKER_PROC], capture_output=True)
    if proc.returncode != 0:
        return []
    return [
        line.strip()
        for line in proc.stdout.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]


def _check_api07(pre_pids):
    """CTX-API-07: the MAP run leaves no orphan worker behind."""
    time.sleep(2)
    post = _worker_pids()
    ok = not pre_pids and not post
    return _check(
        "CTX-API-07: no orphan context_worker.py after the MAP run",
        ok,
        "workers before=%d after=%d" % (len(pre_pids), len(post)),
    )


def _run_prompt_with_env(env, workspace, prompt, label, env_overrides):
    """Sibling of ``_run_prompt``: merge ``env_overrides`` into a *copy* of env."""
    child_env = dict(env)
    child_env.update(env_overrides or {})
    offset = os.path.getsize(STUB_OUT) if os.path.isfile(STUB_OUT) else 0
    proc = subprocess.run(
        ["opencode", "run", "--standalone", "--auto", "-m", "stub/stub-1", prompt],
        cwd=workspace,
        env=child_env,
        capture_output=True,
        timeout=RUN_TIMEOUT,
    )
    print("[opencode] %s run exit=%s" % (label, proc.returncode))

    bodies, raw = _read_appended(offset)
    print("[stub] captured %d request body/bodies from the %s run" % (len(bodies), label))
    return proc, bodies, raw


def _check_ctx07(env, workspace):
    """CTX-07: HARNESS_CTX=off disables injection entirely."""
    proc, bodies, _raw = _run_prompt_with_env(
        env, workspace, PROMPT, "ctx07-kill-switch", {"HARNESS_CTX": "off"}
    )
    primary = _primary_body(bodies)
    text = _body_text(primary)
    marker_present = MARKER in text
    source_line_present = "# source:" in text
    ok = (
        len(bodies) > 0
        and primary is not None
        and not marker_present
        and not source_line_present
        and proc.returncode == 0
    )
    return _check(
        "CTX-07: HARNESS_CTX=off disables injection entirely",
        ok,
        "captured=%d marker=%s source_line=%s exit=%s"
        % (len(bodies), marker_present, source_line_present, proc.returncode),
    )


def _check_ctx12(env, workspace):
    """CTX-12: a self-contained snippet must not trigger repo lookup."""
    pre = _count_workers()
    bodies, raw = _run_prompt(env, workspace, SNIPPET_PROMPT, "ctx12-snippet")
    time.sleep(1)
    post = _count_workers()
    primary = _primary_body(bodies)
    text = _body_text(primary)
    marker_present = MARKER in text
    source_line_present = "# source:" in text
    marker_in_raw = MARKER in raw.decode("utf-8", "replace")
    ok = (
        len(bodies) > 0
        and primary is not None
        and not marker_present
        and not marker_in_raw
        and not source_line_present
        and pre == 0
        and post == 0
    )
    return _check(
        "CTX-12: self-contained snippet injects nothing and spawns no worker",
        ok,
        "captured=%d marker=%s source_line=%s workers before=%d after=%d"
        % (len(bodies), marker_present, source_line_present, pre, post),
    )


def _check_ctx13(env, workspace):
    """CTX-13: naming a known file gates FOCUSED, never MAP."""
    pre = _count_workers()
    bodies, _raw = _run_prompt(env, workspace, PROMPT, "ctx13-focused")
    time.sleep(1)
    post = _count_workers()
    primary = _primary_body(bodies)
    text = _body_text(primary)
    focused_packet = "# source: ctx_fixtures/hello.txt" in text
    ranked_map = "# source: <ranked-map>" in text
    ok = (
        len(bodies) > 0
        and primary is not None
        and focused_packet
        and not ranked_map
        and pre == 0
        and post == 0
    )
    return _check(
        "CTX-13: known file gates FOCUSED (file packet, no ranked map, no worker)",
        ok,
        "captured=%d focused_packet=%s ranked_map=%s workers before=%d after=%d"
        % (len(bodies), focused_packet, ranked_map, pre, post),
    )


def _check_ctx16(env, workspace):
    """CTX-16: the MAP packet is present, budgeted, and carries real entries."""
    bodies, _raw = _run_prompt(env, workspace, MAP_PROMPT, "ctx16-map-budget")
    primary = _primary_body(bodies)
    text = _body_text(primary)
    ranked_map = "# source: <ranked-map>" in text
    packet = _packet_text(primary)
    packet_bytes = len(packet.encode("utf-8")) if packet is not None else -1
    defs = MAP_DEF_LINE_RE.findall(packet) if packet is not None else []
    ok = (
        len(bodies) > 0
        and primary is not None
        and ranked_map
        and packet is not None
        and 0 < packet_bytes <= MAP_PACKET_MAX_BYTES
        and len(defs) >= 3
    )
    return _check(
        "CTX-16: ranked-map packet is present, <= 8 KiB, with >= 3 def entries",
        ok,
        "captured=%d ranked_map=%s packet_bytes=%d defs=%d"
        % (len(bodies), ranked_map, packet_bytes, len(defs)),
    )


def _check_ctx17(env, workspace):
    """CTX-17: ten repeated general turns stay NONE — no packet, no worker."""
    marker_runs = 0
    total_bodies = 0
    for turn in range(10):
        bodies, _raw = _run_prompt(env, workspace, GEN_PROMPT, "ctx17-general-%d" % (turn + 1))
        total_bodies += len(bodies)
        primary = _primary_body(bodies)
        if primary is not None and MARKER in _body_text(primary):
            marker_runs += 1
    time.sleep(1)
    workers = _count_workers()
    ok = total_bodies > 0 and marker_runs == 0 and workers == 0
    return _check(
        "CTX-17: ten general turns inject nothing and spawn no worker",
        ok,
        "runs=10 captured=%d marker_runs=%d workers=%d" % (total_bodies, marker_runs, workers),
    )


def _check_ctx06(env, workspace, plugin_dir):
    """CTX-06: an unavailable worker fails open — no map, no crash, no orphan."""
    worker_path = os.path.join(plugin_dir, WORKER_PROC)
    hidden_path = worker_path + ".bak"
    if not os.path.isfile(worker_path):
        return _check(
            "CTX-06: missing worker fails open (no map, no crash, no orphan)",
            False,
            "context_worker.py not found in %s — cannot hide it" % plugin_dir,
        )

    os.rename(worker_path, hidden_path)
    try:
        offset = os.path.getsize(STUB_OUT) if os.path.isfile(STUB_OUT) else 0
        proc = subprocess.run(
            ["opencode", "run", "--standalone", "--auto", "-m", "stub/stub-1", MAP_PROMPT],
            cwd=workspace,
            env=env,
            capture_output=True,
            timeout=RUN_TIMEOUT,
        )
        print("[opencode] ctx06-worker-hidden run exit=%s" % proc.returncode)
        bodies, _raw = _read_appended(offset)
        print("[stub] captured %d request body/bodies from the ctx06 run" % len(bodies))
        time.sleep(2)
        workers = _count_workers()
    finally:
        if os.path.isfile(hidden_path):
            os.rename(hidden_path, worker_path)

    primary = _primary_body(bodies)
    text = _body_text(primary)
    ranked_map = "# source: <ranked-map>" in text
    ok = (
        len(bodies) > 0
        and primary is not None
        and not ranked_map
        and proc.returncode == 0
        and workers == 0
    )
    return _check(
        "CTX-06: missing worker fails open (no map, no crash, no orphan)",
        ok,
        "captured=%d ranked_map=%s exit=%s workers=%d"
        % (len(bodies), ranked_map, proc.returncode, workers),
    )


def main():
    for label, path in (
        ("stub provider", STUB_PATH),
        ("plugin", PLUGIN),
        ("fixture", FIXTURE_SRC),
        ("lib fixture", LIB_FIXTURE_SRC),
        ("map fixture", MAP_FIXTURE_SRC),
        ("context worker", WORKER_SRC),
        ("harness_core", HARNESS_CORE_SRC),
    ):
        if not os.path.exists(path):
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
        os.makedirs(os.path.join(workspace, "ctx_fixtures", "lib"))
        shutil.copyfile(
            LIB_FIXTURE_SRC, os.path.join(workspace, "ctx_fixtures", "lib", "util.py")
        )
        # CTX Day 5: a named-symbol fixture so the ranked map has at least
        # one non-trivial Python file to rank in the MAP workspace.
        shutil.copyfile(
            MAP_FIXTURE_SRC, os.path.join(workspace, "ctx_fixtures", "map_fixture.py")
        )
        # A second source language: repomap_adapter only emits its
        # `# languages:` header when more than one language contributes
        # files, and CTX-API-04 asserts that line is present.
        with open(
            os.path.join(workspace, "ctx_fixtures", "worker_helper.js"),
            "w",
            encoding="utf-8",
        ) as fh:
            fh.write(MAP_JS_FIXTURE)

        # OpenCode v2.0.24 rejects a bare file in `plugins`
        # ("configured plugin path must be a directory"), so hand it a
        # plugin directory holding a copy of the plugin under test.
        plugin_dir = os.path.join(config_dir, "plugins")
        os.makedirs(plugin_dir)
        shutil.copyfile(PLUGIN, os.path.join(plugin_dir, "auto-context.ts"))

        # CTX Day 5: the plugin resolves the worker next to itself
        # (`WORKER_DIR/context_worker.py`) with `../harness_core` as its
        # import root, so lay out the same shape install.js produces.
        shutil.copyfile(
            WORKER_SRC, os.path.join(plugin_dir, "context_worker.py")
        )
        shutil.copytree(
            HARNESS_CORE_SRC,
            os.path.join(config_dir, "harness_core"),
            ignore=shutil.ignore_patterns("__pycache__"),
        )

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

        # Isolation: the worker is spawned as a bare `python3` by the plugin,
        # so the repo venv must lead the child's PATH even when the caller
        # never activated it.  Without this the worker cannot import
        # grep_ast/networkx/tree_sitter, silently fails open, and the MAP
        # packet (CTX-API-04) is never produced.  Prepended here so every
        # `opencode run` below (primary, general-conversation, missing-file,
        # lib-fixture, map-repo-question) inherits the same corrected PATH.
        venv_bin = os.path.join(REPO_ROOT, "venv", "bin")
        existing_path = os.environ.get("PATH", "/usr/bin:/bin")
        env["PATH"] = venv_bin + os.pathsep + existing_path

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

        # CTX-API-02 makes the zero-tool-call claim explicit on that same run:
        # the stub never asks for repo data of its own, yet the packet is in
        # the primary request the model actually sees.
        raw_text = raw.decode("utf-8", "replace")
        ok &= _check(
            "CTX-API-02: zero-tool-call model still receives the packet",
            MARKER in raw_text
            and "# source: ctx_fixtures/hello.txt" in raw_text,
            "model=stub (no tool calls), packet_present=%s" % (MARKER in raw_text),
        )

        ok &= _check_api05(bodies)
        ok &= _check_api03(env, workspace)

        # Fingerprint of the packet the first run injected, so the REUSE run
        # can prove it did not change the file or duplicate the packet.
        first_source, first_digest = _primary_packet(_primary_body(bodies))
        print("[packet] first run source=%s sha256=%s" % (first_source, first_digest))

        # CTX Day 3+4 checks: isolated second/third/fourth turns.
        ok &= _check_api06(env, workspace, first_source, first_digest)
        ok &= _check_api08(env, workspace)
        ok &= _check_api09(env, workspace)

        # CTX Day 5 checks: MAP branch + worker lifecycle.  Record worker
        # PIDs before the MAP run so CTX-API-07 can prove the plugin cleans
        # up after itself (brief: should be none).
        pre_worker_pids = _worker_pids()
        ok &= _check_api04(env, workspace)
        ok &= _check_api07(pre_worker_pids)

        # CTX Days 6-7 checks: kill switch, snippet gate, FOCUSED-vs-MAP,
        # MAP budget, repeated general turns, and worker fail-open.
        ok &= _check_ctx07(env, workspace)
        ok &= _check_ctx12(env, workspace)
        ok &= _check_ctx13(env, workspace)
        ok &= _check_ctx16(env, workspace)
        ok &= _check_ctx17(env, workspace)
        ok &= _check_ctx06(env, workspace, plugin_dir)

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
