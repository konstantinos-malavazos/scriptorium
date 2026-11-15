---
name: bot-standards
description: >-
  The scriptorium bot layer (Python + Markdown). The coding + review standard for
  bot/Python + Markdown. Loaded when
  writing or reviewing Python + Markdown code, or when running as the bot-specialist agent.
  Deliberately carries NO rules for other layers so it stays cheap to load.
---

# bot/Python + Markdown engineering standards

> Keep each layer's standards scoped to that layer only. A schema specialist should not
> carry frontend rules. A frontend specialist should not carry SQL index rules. Scope by
> language/layer: do not trim content, split it.

## Severity model (shared across all layers)
- `[BLOCKER]` — must fix before merge (correctness, security, data loss).
- `[MAJOR]` — should fix (design, maintainability, missing tests).
- `[MINOR]` — worth fixing (readability, small inefficiency).
- `[NIT]` — optional (style, naming).

## Correctness & design
- <e.g. layer boundaries respected; no business logic in the wrong layer>
- <e.g. inputs validated at the boundary; failure modes handled>

## <Language-specific rules>
- <e.g. async everywhere IO happens; cancellation honoured; no sync-over-async>
- <e.g. nullability handled; disposal correct>
- <e.g. for SQL: index coverage & selectivity; SARGable predicates; no N+1>

## Testing
- <what must be tested at this layer, and how (unit / integration / behaviour)>
- Test behaviour through public interfaces, not internals.

## Security & logging
- No hardcoded secrets. Mask/pseudonymize sensitive identifiers in logs.
- No logging of credentials, tokens, or raw PII.

## Compile/build correctness
- The change must build and the relevant tests must pass before hand-off.
- Resolve every member you call with `find_symbol`; never assume it exists or what it takes.
- Run `get_diagnostics_for_file` on each changed file, before the build.

## Comments
- A comment clarifies the code, or it does not exist: only a non-obvious why, a trap, or a
  contract a caller relies on.
- `[MAJOR]` — any narrating comment: one that restates what the code does, or carries
  ticket ids, plan steps, grilling answers, review findings or build history. This covers
  code comments, docstrings and HTML comments in Markdown. That history belongs in the
  commit message and the handoffs.

## Vendoring note
If you have an authoritative in-house standard, vendor it here **verbatim** and pin it to
a specific commit, so the skill matches the source of truth exactly.
