# Claim Bundle AI - Privacy and Provider Handling

The staged Claim Bundle feature is optional. Manual Claims remains fully
functional when AI is disabled or no provider key is configured.

## Data flow

1. The complete document remains in the local Casefile document store.
2. Page-aware text is extracted locally. Pages without sufficient embedded
   text are marked `OCR_REVIEW_REQUIRED`; they are not silently treated as
   empty evidence.
3. Only the explicitly configured provider receives the page/range text needed
   for classification or extraction. Casefile does not send a bundle to
   multiple providers automatically.
4. Reconciliation arithmetic, validation, permissions, persistence, query
   creation and Claim decisions remain deterministic local operations.

## Stored audit data

Casefile stores prompt/schema versions, provider/model, document hash, logical
page range, token counts, latency, cost metadata, extracted facts, short source
snippets, user review decisions and structured conflicts. It does not request
or store chain-of-thought. API keys remain environment-only and are never
returned by the API.

## Confidential benchmark

`backend/tests/fixtures/claims_real/*.pdf` is ignored by Git. Only reviewed
metadata and expected-result JSON may be committed. Live provider benchmarks
require explicit opt-in; normal regression tests use mocked responses and do
not incur provider cost.

## Human control

AI may propose facts, discrepancies, missing-document suggestions and draft
query wording. It cannot admit, partly admit, reject, or automatically email a
Claim. Conflict-linked facts are excluded from bulk acceptance. The responsible
professional confirms or corrects values before they reach the canonical Claim.
