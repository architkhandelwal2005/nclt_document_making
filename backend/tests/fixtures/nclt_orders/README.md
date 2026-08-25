# Locked NCLT order regression fixtures

These files were prepared by inspecting the supplied source PDFs with
`pdfplumber` page text and rendered-page review. The application admission
parser was not invoked and its output was not used.

Status: `LOCKED_REGRESSION_FIXTURE`

The project owner approved and locked these expectations on 2026-08-23. The
PDFs under `source/` and JSON contracts under `expected/` are active regression
fixtures. Parser output must be compared against these files; parser code and
tests must never regenerate or rewrite the expected JSON.

Source conflicts and raw extraction defects are intentionally retained as
provenance. Their approved resolutions are part of the fixture contract.
