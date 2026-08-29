# Phase 4 UAT: CIRP-057--076

## Operations and management

1. Create a going-concern assessment. Confirm forecast cash fields retain two decimal places and require a professional approval action.
2. Create a cash-flow period, add forecast and actual entries, finalise it, and verify final records reject ordinary edits.
3. Create a receivable, record follow-up and collection. Confirm the outstanding figure updates without rounding drift.
4. Record an asset movement against an existing asset and confirm an asset from another case is rejected.
5. Add a statutory compliance item and a moratorium review. Verify both remain `REVIEW_REQUIRED` unless a professional makes a decision.
6. Issue a management requisition, add a missing response and reminder, then create a Section 19 draft. Confirm professional confirmation is compulsory before draft/final/filing.

## Valuation and IM

1. Record a valuation requirement, quotation/comparison, appointment, declaration and assignment.
2. Attempt to activate the assignment before declaration verification; it must fail.
3. Verify the declaration and activate the assignment. Confirm appointment and declaration IDs are retained in the assignment.
4. Initialize the IM. Review its source-linked checklist and mark unavailable source material for review rather than entering guessed information.
5. Create an IM version. Confirm the legacy IM office format is shown as template conversion/verification required, not generated automatically.

## Confidentiality and VDR

1. Record an undertaking and attempt a VDR grant before verification; it must fail.
2. Verify the undertaking, grant a recipient limited folder access, and confirm an audit entry is present.
3. Revoke access with a reason; confirm subsequent access checks fail.
4. Publish a restricted valuation document to the VDR and attempt generic viewer download. It must be denied.

## Cost allocation

1. Create a CoC cost statement with period, cost kind, approval and payment data.
2. Create an allocation snapshot only after confirmed CoC membership exists.
3. Confirm allocation totals equal the cost statement total exactly and that the snapshot cannot be regenerated with later membership changes.
