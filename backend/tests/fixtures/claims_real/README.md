# Real Claim Bundle Fixture

Place the confidential local benchmark PDF at:

`backend/tests/fixtures/claims_real/mahakali_form_c_with_annexures.pdf`

The PDF is ignored by Git. `mahakali.expected.json` is independently reviewed
ground truth and must never be regenerated from AI/parser output merely to make
a benchmark pass. Live provider runs require explicit opt-in; ordinary tests
use mocked provider output and remain free.
