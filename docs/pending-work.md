
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
