
## T03 related mode — qualified call-site resolution
**Discovered:** 2026-10-10
**Effort:** ~1-2 hr
**Priority:** low (cosmetic noise on real repos)

`_get_body_references` in `find_dependent_references` mode="related"
resolves references by the last attribute name only. So `x.save()`
and `x.foo.save()` both map to any definition named `save`. This is
fine for small repos but creates noise when many objects share a
method name.

Fix: track the full qualified path for Attribute references
(e.g., `x.foo.save` → look up `foo.save` and `save` candidates).
Requires resolving `x` back to its import or assignment — real
semantic analysis. Defer until a real user hits it.

## Session Y follow-ups

**Discovered:** 2026-10-10

1. **Restore CTX smoke stub** — `/tmp/opencode/ctx_spike/stub_provider.py`
   disappeared (likely /tmp cleanup). The 22 stub-based CTX end-to-end
   checks in tests/test_ctx_smoke.py skip without it. The 2 new
   CTX-API-10/11 tests (synthetic messages) pass regardless.
   Recreate from docs/ctx-spike-findings.md §6 to re-verify the 22.

2. **Provider usage via ctx.event.subscribe** — the SDK exposes
   `session.usage.updated` / `session.usage.recorded` events with
   `tokens{input,output,reasoning,cache{read,write}}` + sessionID, but
   no providerID/modelID, no per-call identity, and no documented
   delta-vs-total semantics. Do NOT build an observer until those are
   resolved — would risk double-counting (MET-01).
   Decision needed: treat as source of truth, or keep provider
   tracking as "unknown"?

3. **INSTALL-08 gap** — installer does not copy LICENSES/ and
   THIRD_PARTY_NOTICES.md to the target. Logged during Session X.
   Fix: add two `copyFileSync` calls in bin/install.js.
