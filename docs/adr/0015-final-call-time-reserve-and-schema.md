# ADR-0015: Reserve time for the final call and bind it to the draft schema

- Status: accepted (autopilot, Phase 8)
- Context: on `local` (qwen3:8b, CPU) every agent run ended as RULES_FALLBACK. In the traced run (EO-01) four tool turns plus a prose reply used 989 s, past the 900 s specialist wall limit, so the final JSON call was refused before it was sent. The model was also free to answer in prose.
- Decision: (1) `_investigate` stops calling tools when two of the slowest observed calls would no longer fit in the wall limit. (2) The final and repair calls may run to 1.5x the wall limit. (3) Tool-less specialist calls pass the `RecommendationDraft` JSON schema; `LiveClient` binds it as Ollama `format`, so decoding is constrained. Replay and cassette keys are unchanged (the schema is not part of the key); other providers ignore it.
- Not changed: labels, formulas, thresholds, gates, `expected:` blocks, wall limit values.
- Consequences: slow models give up some investigation turns to guarantee an answer. A run can exceed the nominal wall limit by up to 50%.
