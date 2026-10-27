# ADR-0014: Gemini API free tier replaces GitHub Models for `free-cloud`

- Status: accepted (owner decision, 2026-10-27)
- Context: GitHub Models was retired on 2026-07-30, so `free-cloud` had no provider (ADR-0002). The local
  profile (`qwen3:8b` on a laptop CPU) is too slow to record the eval suite and often returns malformed JSON,
  so the Phase 6 agent run fell back to rules on most scenarios.
- Decision: point `free-cloud` at the Gemini API free tier through its OpenAI-compatible endpoint
  (`https://generativelanguage.googleapis.com/v1beta/openai/`) using the existing `openai` provider path.
  No new dependency. Key from Google AI Studio with no billing attached, read from `GEMINI_API_KEY`.
- Models: `gemini-3.5-flash` for specialist, extractor and writer (passed a tool-call smoke test on
  2026-10-27; 3.6-3.8 Flash were overloaded and 2.5 Flash is closed to new users). Judge
  `gemini-3.5-flash-lite`. The calibrated judge stays `gemma4:e4b` on the `local` profile.
- Limits: `rpm: 8`, `concurrency: 1`. Measured 2026-10-27: the free tier allows only **20 requests per day**
  for `gemini-3.5-flash` (served as 3.6 Flash). One smoke eval needs ~60 requests, so the free tier can drive
  single demo runs but not the eval suites. Recording evals needs a paid tier, a higher-quota model, or the
  `local` profile.
- Thought signatures: Gemini 3 returns `extra_content.google.thought_signature` on every tool call and rejects
  the next turn with HTTP 400 if it is missing. The `openai` provider path uses the `openai` SDK directly and
  carries provider fields in `ToolCall.extra`; cassette keys exclude it. Final JSON is valid with
  `reasoning_effort: low`.
- Consequences: free-tier prompts may be used by Google to improve its products; all data here is synthetic.
  CI live and nightly evals need a `GEMINI_API_KEY` repository secret, set by the owner.
