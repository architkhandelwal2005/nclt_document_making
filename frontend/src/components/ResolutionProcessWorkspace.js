import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, ArrowRight, CheckCircle2, ClipboardCheck, FileKey2,
  Gavel, Layers3, ListChecks, RefreshCw, Scale, ShieldCheck, Users,
} from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

const REGISTER_TYPES = [
  "EOI_PROCESS", "ELIGIBILITY_CRITERION", "PRA", "CONSORTIUM_MEMBER", "EOI_SUBMISSION",
  "EOI_CHECKLIST", "PROCESS_DEPOSIT", "ELIGIBILITY_REVIEW", "CONNECTED_PERSON",
  "PROVISIONAL_LIST", "ELIGIBILITY_OBJECTION", "FINAL_LIST", "RFRP", "EVALUATION_MATRIX",
  "ISSUE_PACKAGE", "PRA_QUERY", "SITE_VISIT", "PROCESS_ADDENDUM", "RESOLUTION_PLAN",
  "SECTION30_REVIEW", "PLAN_REVIEW_QUERY", "PLAN_EVALUATION", "NEGOTIATION_PROCESS",
  "NEGOTIATION_ROUND", "NEGOTIATION_SUBMISSION", "COC_PLAN_PLACEMENT", "PLAN_VOTE_LINK",
  "SUCCESSFUL_RA", "PLAN_APPROVAL_WORKSPACE", "HEARING_LINK",
];

const DETAILED_REGISTER_TYPES = new Set([
  "EOI_CHECKLIST", "ELIGIBILITY_REVIEW", "PROVISIONAL_LIST", "FINAL_LIST",
  "EVALUATION_MATRIX", "SECTION30_REVIEW", "PLAN_EVALUATION",
]);

const STAGES = [
  { id: "overview", label: "Control room", subtitle: "Status and next gates", icon: Layers3, types: [] },
  { id: "process", label: "EOI process", subtitle: "Criteria, approval, publication", icon: FileKey2, types: ["EOI_PROCESS", "ELIGIBILITY_CRITERION"] },
  { id: "applicants", label: "PRA & EOI", subtitle: "Applicants, consortiums, receipts", icon: Users, types: ["PRA", "CONSORTIUM_MEMBER", "EOI_SUBMISSION", "EOI_CHECKLIST", "PROCESS_DEPOSIT"] },
  { id: "eligibility", label: "Eligibility & lists", subtitle: "Section 29A, objections, final list", icon: ShieldCheck, types: ["ELIGIBILITY_REVIEW", "CONNECTED_PERSON", "PROVISIONAL_LIST", "ELIGIBILITY_OBJECTION", "FINAL_LIST"] },
  { id: "bid", label: "RFRP & bid period", subtitle: "Matrix, VDR, queries, addenda", icon: ListChecks, types: ["RFRP", "EVALUATION_MATRIX", "ISSUE_PACKAGE", "PRA_QUERY", "SITE_VISIT", "PROCESS_ADDENDUM"] },
  { id: "plans", label: "Plans & compliance", subtitle: "Versions, Section 30(2), final 29A", icon: ClipboardCheck, types: ["RESOLUTION_PLAN", "SECTION30_REVIEW", "PLAN_REVIEW_QUERY"] },
  { id: "decision", label: "Evaluation & CoC", subtitle: "Scoring, negotiation, vote, SRA", icon: Scale, types: ["PLAN_EVALUATION", "NEGOTIATION_PROCESS", "NEGOTIATION_ROUND", "NEGOTIATION_SUBMISSION", "COC_PLAN_PLACEMENT", "PLAN_VOTE_LINK", "SUCCESSFUL_RA", "PROCESS_DEPOSIT"] },
  { id: "nclt", label: "NCLT approval", subtitle: "Application, hearing and order", icon: Gavel, types: ["PLAN_APPROVAL_WORKSPACE", "HEARING_LINK"] },
];

const option = (value, label) => ({ value, label });
const options = values => values.map(value => option(value, value.replaceAll("_", " ")));
const field = (key, label, type = "text", extra = {}) => ({ key, label, type, ...extra });
const record = (key, label, recordTypes, extra = {}) => field(key, label, "record", { recordTypes: Array.isArray(recordTypes) ? recordTypes : [recordTypes], ...extra });
const documentField = (key, label, extra = {}) => field(key, label, "document", extra);
const confirmation = field("professional_confirmed", "I confirm this professional/legal decision", "checkbox", { required: true, caution: true });

const ACTIONS = [
  {
    id: "eoi_process", stage: "process", title: "Create EOI process version", submit: "Create draft version",
    note: "All commercial terms remain case-specific. No precedent values are inserted.",
    fields: [field("status", "Starting status", "select", { options: options(["DRAFT", "COC_REVIEW", "APPROVED"]), default: "DRAFT" }), field("EOI_due_date", "EOI due date", "date", { required: true }), field("submission_modes", "Submission modes", "text", { placeholder: "EMAIL, PHYSICAL", split: true }), field("deposit_required", "Deposit required", "checkbox"), field("deposit_type", "Deposit requirement / type"), field("deposit_amount", "Deposit amount", "number", { step: "0.01" }), field("deposit_currency", "Currency", "text", { default: "INR" }), field("late_submission_policy", "Late-submission policy", "textarea", { full: true }), field("consortium_allowed", "Consortium allowed", "checkbox"), field("RFRP_target_date", "RFRP target date", "date"), field("plan_submission_target_date", "Plan target date", "date"), field("idempotency_key", "Control reference", "text", { autoKey: "eoi-process" })],
  },
  {
    id: "eligibility_criterion", stage: "process", title: "Add eligibility criterion", submit: "Add criterion",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true, mutableOnly: true }), field("title", "Criterion title", "text", { required: true }), field("description", "Description", "textarea", { full: true }), field("criterion_type", "Criterion type", "select", { options: options(["NUMERIC", "BOOLEAN", "TEXT", "DOCUMENT", "OTHER"]), required: true }), field("operator", "Operator", "select", { options: options(["", "GREATER_THAN", "GREATER_THAN_OR_EQUAL", "EQUAL", "LESS_THAN", "OTHER"]) }), field("threshold", "Threshold / value"), field("currency", "Currency"), field("unit", "Unit"), field("applies_to", "Applies to", "select", { options: options(["PRA", "PROMOTER", "PROMOTER_GROUP", "CONSORTIUM", "LEAD_MEMBER", "ANY_MEMBER", "OTHER"]), default: "PRA" }), field("supporting_document_requirement", "Supporting-document requirement", "textarea", { full: true })],
  },
  {
    id: "eoi_approve", stage: "process", title: "Approve EOI process", submit: "Confirm approval",
    note: "At least one entered eligibility criterion is required.",
    confirm: "Approve this EOI process version? Its criteria and entered terms will become the approved basis for publication.",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true, statuses: ["DRAFT", "COC_REVIEW", "APPROVED"] }), field("coc_meeting_id", "CoC meeting", "meeting"), field("coc_resolution_id", "CoC resolution", "cocResolution"), field("approval_note", "Professional / CoC approval note", "textarea", { full: true }), confirmation],
  },
  {
    id: "eoi_publish", stage: "process", title: "Publish approved EOI", submit: "Publish and freeze",
    note: "The final EOI and publication proof must first be uploaded in Documents.",
    confirm: "Publish and freeze this EOI version? Later changes require a new version or addendum.",
    fields: [record("process_id", "Approved EOI process", "EOI_PROCESS", { required: true, statuses: ["APPROVED"] }), documentField("document_id", "Final EOI document", { required: true }), documentField("publication_proof_document_id", "Publication / dispatch proof", { required: true }), field("publication_date", "Publication date", "date", { required: true })],
  },
  {
    id: "eoi_revise", stage: "process", title: "Create revised EOI draft", submit: "Create revised version",
    fields: [record("process_id", "Approved/published process", "EOI_PROCESS", { required: true, statuses: ["APPROVED", "PUBLISHED"] }), field("EOI_due_date", "Revised EOI due date", "date"), field("reason", "Revision reason", "textarea", { required: true, full: true }), field("idempotency_key", "Control reference", "text", { autoKey: "eoi-revision" })],
  },
  {
    id: "pra", stage: "applicants", title: "Create PRA master", submit: "Create PRA",
    fields: [field("pra_type", "PRA type", "select", { options: options(["COMPANY", "LLP", "PARTNERSHIP", "TRUST", "FUND", "INDIVIDUAL", "CONSORTIUM", "OTHER"]), default: "COMPANY", required: true }), field("legal_name", "Legal name", "text", { required: true }), field("registration_number", "CIN / LLPIN / registration"), field("pan", "PAN / tax ID"), field("registered_office", "Registered office", "textarea", { full: true }), field("contact_person", "Contact person"), field("email", "Email", "email"), field("phone", "Phone"), field("parent_entity", "Parent entity"), field("ultimate_parent", "Ultimate parent"), field("status", "Status", "select", { options: options(["ACTIVE", "WITHDRAWN", "INACTIVE"]), default: "ACTIVE" }), field("idempotency_key", "Control reference", "text", { autoKey: "pra" })],
  },
  {
    id: "consortium_member", stage: "applicants", title: "Add consortium member", submit: "Add member",
    fields: [record("consortium_id", "Consortium PRA", "PRA", { required: true, dataFilter: item => item.data?.pra_type === "CONSORTIUM" }), field("member_legal_name", "Member legal name", "text", { required: true }), field("registration_number", "Registration number"), field("lead_member", "Lead member", "checkbox"), field("percentage_holding", "Participation percentage", "number", { min: 0, max: 100, step: "0.01" }), field("financials_relied_upon", "Financials relied upon", "checkbox"), documentField("agreement_document", "Consortium agreement"), documentField("authorization_document", "Authorization document")],
  },
  {
    id: "eoi_submission", stage: "applicants", title: "Register EOI receipt", submit: "Register EOI",
    fields: [record("process_id", "Published EOI process", "EOI_PROCESS", { required: true, statuses: ["PUBLISHED"] }), record("pra_id", "PRA", "PRA", { required: true }), field("received_at", "Received at", "datetime-local", { required: true, now: true }), field("submission_channel", "Channel", "select", { options: options(["EMAIL", "PHYSICAL", "PORTAL", "OTHER"]), default: "EMAIL" }), field("hard_copy_received_at", "Hard copy received at", "datetime-local"), field("submission_status", "Receipt status", "select", { options: options(["RECEIVED", "LATE", "INCOMPLETE", "UNDER_SCRUTINY", "WITHDRAWN"]), default: "RECEIVED" }), field("receipt_reference", "Receipt reference"), field("remarks", "Remarks", "textarea", { full: true }), field("idempotency_key", "Unique receipt reference", "text", { autoKey: "eoi-receipt", required: true })],
  },
  {
    id: "eoi_checklist", stage: "applicants", title: "Record EOI document checklist", submit: "Create checklist",
    fields: [record("submission_id", "EOI submission", "EOI_SUBMISSION", { required: true })],
    repeaters: [{ key: "items", label: "Document checklist", addLabel: "Add checklist item", fields: [field("item_key", "Document category", "text", { required: true }), field("status", "Status", "select", { options: options(["REQUIRED", "RECEIVED", "DEFICIENT", "NOT_APPLICABLE", "REVIEW_REQUIRED", "ACCEPTED"]), default: "REQUIRED" }), field("notes", "Review note")] }],
  },
  {
    id: "deposit", stage: "applicants", title: "Record deposit / bank guarantee", submit: "Record instrument",
    fields: [record("process_id", "Process", ["EOI_PROCESS", "RFRP"]), record("pra_id", "PRA", "PRA", { required: true }), field("deposit_type", "Type", "select", { options: options(["EOI_DEPOSIT", "PLAN_DEPOSIT", "PERFORMANCE_SECURITY", "OTHER"]), default: "EOI_DEPOSIT" }), field("required_amount", "Required amount", "number", { step: "0.01" }), field("received_amount", "Received amount", "number", { step: "0.01" }), field("currency", "Currency", "text", { default: "INR" }), field("instrument_type", "Instrument", "select", { options: options(["DD", "BANK_GUARANTEE", "BANK_TRANSFER", "OTHER"]), default: "BANK_GUARANTEE" }), field("instrument_reference", "Instrument reference"), field("bank", "Bank / issuer"), field("issue_date", "Issue date", "date"), field("expiry_date", "Expiry date", "date"), field("received_date", "Received date", "date"), field("status", "Status", "select", { options: options(["NOT_REQUIRED", "REQUIRED", "RECEIVED", "VERIFIED", "DEFICIENT", "RETURN_DUE", "RETURNED", "FORFEITURE_REVIEW", "FORFEITED"]), default: "RECEIVED" }), documentField("document_id", "Instrument document"), field("professional_confirmed", "Professional/CoC decision confirmed (required only for forfeiture)", "checkbox"), field("idempotency_key", "Control reference", "text", { autoKey: "deposit" })],
  },
  {
    id: "eligibility_review", stage: "eligibility", title: "Start Section 29A review", submit: "Start review",
    fields: [record("process_id", "EOI process", "EOI_PROCESS"), record("submission_id", "EOI submission", "EOI_SUBMISSION"), record("pra_id", "PRA or consortium member", ["PRA", "CONSORTIUM_MEMBER"], { required: true }), field("review_stage", "Review stage", "select", { options: options(["EOI_INITIAL", "PROVISIONAL_LIST", "FINAL_LIST", "PLAN_SUBMISSION_RECHECK", "PRE_COC_VOTE_RECHECK"]), default: "EOI_INITIAL" }), field("status", "Starting status", "select", { options: options(["IN_PROGRESS", "MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED", "REVIEW_REQUIRED"]), default: "IN_PROGRESS" }), field("legal_reviewer", "Legal reviewer"), field("professional_note", "Professional note", "textarea", { full: true })],
    repeaters: [{ key: "checklist", label: "Section 29A checklist", addLabel: "Add legal checklist item", fields: [field("section", "Section / reference", "text", { required: true }), field("question", "Question", "text", { required: true }), field("response", "Response", "select", { options: options(["YES", "NO", "UNKNOWN", "NOT_APPLICABLE"]), default: "UNKNOWN" }), field("legal_review_required", "Legal review required", "checkbox"), field("reviewer_note", "Reviewer note")] }],
  },
  {
    id: "connected_person", stage: "eligibility", title: "Record connected person", submit: "Record person",
    fields: [record("pra_id", "PRA or consortium member", ["PRA", "CONSORTIUM_MEMBER"], { required: true }), field("name", "Person / entity name", "text", { required: true }), field("relationship", "Relationship", "text", { required: true }), field("beneficial_owner", "Beneficial owner", "select", { options: options(["YES", "NO", "UNKNOWN"]), default: "UNKNOWN" }), field("verification_status", "Verification", "select", { options: options(["UNVERIFIED", "VERIFIED", "REVIEW_REQUIRED"]), default: "UNVERIFIED" }), field("source", "Source / basis", "textarea", { full: true }), documentField("evidence_document_id", "Evidence document")],
  },
  {
    id: "eligibility_finalize", stage: "eligibility", title: "Finalize eligibility review", submit: "Record professional conclusion",
    confirm: "Record this Section 29A professional conclusion and freeze the review version?",
    fields: [record("review_id", "Eligibility review", "ELIGIBILITY_REVIEW", { required: true, mutableOnly: true }), field("status", "Conclusion", "select", { options: options(["ELIGIBLE", "INELIGIBLE", "CONDITIONALLY_ELIGIBLE", "MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED", "REVIEW_REQUIRED"]), required: true }), field("reason", "Reason / professional note", "textarea", { required: true, full: true }), field("legal_reviewer", "Legal reviewer"), field("review_date", "Review date", "date"), confirmation],
  },
  {
    id: "provisional_list", stage: "eligibility", title: "Generate provisional PRA list", submit: "Generate draft list",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), confirmation],
    repeaters: [{ key: "entries", label: "PRA list entries", addLabel: "Add PRA", fields: [record("pra_id", "PRA", "PRA", { required: true }), record("review_id", "Confirmed review", "ELIGIBILITY_REVIEW", { required: true }), field("result", "Result", "select", { options: options(["ELIGIBLE", "INELIGIBLE", "REVIEW_REQUIRED"]), default: "REVIEW_REQUIRED" }), field("reason", "Reason")] }],
  },
  {
    id: "final_list", stage: "eligibility", title: "Generate final PRA list", submit: "Generate final-list draft",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), confirmation],
    repeaters: [{ key: "entries", label: "Final PRA entries", addLabel: "Add PRA", fields: [record("pra_id", "PRA", "PRA", { required: true }), record("review_id", "Final confirmed review", "ELIGIBILITY_REVIEW", { required: true }), field("result", "Result", "select", { options: options(["ELIGIBLE", "INELIGIBLE"]), default: "ELIGIBLE" }), field("reason", "Reason")] }],
  },
  { id: "list_approve", stage: "eligibility", title: "Approve PRA list", submit: "Approve and freeze", confirm: "Approve and freeze this PRA list version?", fields: [record("list_id", "Draft list", ["PROVISIONAL_LIST", "FINAL_LIST"], { required: true, statuses: ["DRAFT"] })] },
  { id: "list_issue", stage: "eligibility", title: "Issue approved PRA list", submit: "Issue list", confirm: "Issue this approved list to all listed PRAs?", fields: [record("list_id", "Approved list", ["PROVISIONAL_LIST", "FINAL_LIST"], { required: true, statuses: ["APPROVED"] }), documentField("proof_document_id", "Common dispatch proof")] },
  {
    id: "objection", stage: "eligibility", title: "Register eligibility objection", submit: "Register objection",
    fields: [record("provisional_list_id", "Issued provisional list", "PROVISIONAL_LIST", { required: true, statuses: ["ISSUED"] }), record("pra_id", "Objecting PRA", "PRA", { required: true }), field("received_at", "Received at", "datetime-local", { now: true }), field("grounds", "Grounds", "textarea", { required: true, full: true }), documentField("evidence_document_id", "Objection/evidence document"), field("idempotency_key", "Control reference", "text", { autoKey: "objection" })],
  },
  {
    id: "objection_decide", stage: "eligibility", title: "Decide objection", submit: "Record reasoned decision", confirm: "Record and freeze this objection decision?",
    fields: [record("objection_id", "Open objection", "ELIGIBILITY_OBJECTION", { required: true, mutableOnly: true }), field("status", "Decision", "select", { options: options(["ACCEPTED", "REJECTED", "PARTLY_ACCEPTED", "CLOSED"]), required: true }), field("decision_text", "Reasoned decision", "textarea", { required: true, full: true }), documentField("decision_document_id", "Decision/evidence document"), confirmation],
  },
  {
    id: "rfrp", stage: "bid", title: "Create RFRP version", submit: "Create RFRP record",
    note: "Upload the office-approved RFRP in Documents; the software does not generate legal wording.",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), documentField("document_id", "RFRP document", { required: true }), field("title", "Version title", "text", { required: true }), field("remarks", "Review notes", "textarea", { full: true }), field("idempotency_key", "Control reference", "text", { autoKey: "rfrp" })],
  },
  {
    id: "evaluation_matrix", stage: "bid", title: "Create Evaluation Matrix", submit: "Create matrix",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), documentField("document_id", "Approved/source matrix document"), field("title", "Matrix title", "text", { required: true }), field("idempotency_key", "Control reference", "text", { autoKey: "matrix" })],
    repeaters: [{ key: "criteria", label: "CoC-approved evaluation criteria", addLabel: "Add criterion", fields: [field("criterion_id", "Criterion code", "text", { required: true }), field("title", "Criterion title", "text", { required: true }), field("maximum_score", "Maximum score", "number", { required: true, min: 0, step: "0.01" }), field("scoring_type", "Scoring type", "select", { options: options(["NUMERIC_FORMULA", "RANGE", "YES_NO", "MANUAL_PROFESSIONAL", "OTHER"]), default: "NUMERIC_FORMULA" }), field("formula_mode", "Formula mode", "select", { options: options(["DIRECT", "PERCENT_OF_MAX"]), default: "DIRECT" })] }],
  },
  {
    id: "process_document_approve", stage: "bid", title: "Approve RFRP / Matrix", submit: "Approve and lock", confirm: "Approve and freeze this exact document version?",
    fields: [record("record_id", "Draft RFRP or Matrix", ["RFRP", "EVALUATION_MATRIX"], { required: true, statuses: ["DRAFT"] }), field("approval_note", "Approval note", "textarea", { full: true }), field("coc_meeting_id", "CoC meeting", "meeting"), confirmation],
  },
  { id: "process_document_issue", stage: "bid", title: "Mark RFRP / Matrix issued", submit: "Mark issued", fields: [record("record_id", "Approved RFRP or Matrix", ["RFRP", "EVALUATION_MATRIX"], { required: true, statuses: ["APPROVED"] }), field("issued_at", "Issued at", "datetime-local", { now: true })] },
  {
    id: "issue_package", stage: "bid", title: "Issue IM / VDR package", submit: "Issue controlled package",
    note: "Blocked unless the PRA is final-eligible and has a verified confidentiality undertaking.",
    confirm: "Grant VDR access and issue these exact RFRP, Matrix and IM versions?",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), record("pra_id", "Final-eligible PRA", "PRA", { required: true }), record("rfrp_id", "Approved/issued RFRP", "RFRP", { required: true, statuses: ["APPROVED", "ISSUED"] }), record("evaluation_matrix_id", "Approved/issued Matrix", "EVALUATION_MATRIX", { required: true, statuses: ["APPROVED", "ISSUED"] }), field("undertaking_id", "Verified PRA undertaking", "phase4", { domain: "confidentiality", recordType: "UNDERTAKING", statuses: ["VERIFIED"], required: true }), field("im_version_id", "Final IM version", "phase4", { domain: "im", recordType: "IM_VERSION", statuses: ["FINAL", "ISSUED"], required: true }), field("vdr_workspace_id", "VDR workspace", "phase4", { domain: "vdr", recordType: "WORKSPACE", required: true }), field("folder_scope", "VDR folder scope", "text", { default: "IM, RFRP, EVALUATION_MATRIX", split: true }), documentField("proof_document_id", "Dispatch proof"), field("dispatch_idempotency_key", "Dispatch reference", "text", { autoKey: "issue-package" })],
  },
  {
    id: "pra_query", stage: "bid", title: "Register PRA query", submit: "Register query",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), record("pra_id", "PRA", "PRA", { required: true }), field("received_at", "Received at", "datetime-local", { now: true }), field("subject", "Subject", "text", { required: true }), field("query", "Query", "textarea", { required: true, full: true }), field("visibility", "Response visibility", "select", { options: options(["PRIVATE_RESPONSE", "SHARED_WITH_ALL_PRAS", "PROCESS_ADDENDUM_REQUIRED"]), default: "PRIVATE_RESPONSE" }), documentField("document_id", "Source document"), field("idempotency_key", "Control reference", "text", { autoKey: "pra-query" })],
  },
  { id: "pra_query_respond", stage: "bid", title: "Respond to PRA query", submit: "Record reviewed response", fields: [record("query_id", "Open PRA query", "PRA_QUERY", { required: true, mutableOnly: true }), field("response", "Reviewed response", "textarea", { required: true, full: true }), field("response_date", "Response date", "date"), documentField("response_document_id", "Response document")] },
  { id: "site_visit", stage: "bid", title: "Record site visit", submit: "Record visit", fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), record("pra_id", "PRA", "PRA", { required: true }), field("visit_date", "Visit date", "date", { required: true }), field("location", "Location", "text", { required: true }), field("attendees", "Attendees"), field("scope", "Scope / access provided", "textarea", { full: true }), field("status", "Status", "select", { options: options(["PROPOSED", "APPROVED", "RECORDED", "CANCELLED"]), default: "RECORDED" })] },
  {
    id: "addendum", stage: "bid", title: "Issue process addendum", submit: "Issue addendum", confirm: "Issue this immutable addendum to every selected PRA?",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), field("applies_to", "Applies to", "select", { options: options(["EOI", "RFRP", "EVALUATION_MATRIX", "IM", "VDR", "OTHER"]), default: "RFRP" }), field("title", "Addendum title", "text", { required: true }), field("change_summary", "Approved change", "textarea", { required: true, full: true }), documentField("document_id", "Addendum document"), field("pra_ids", "Recipients", "multiRecord", { recordTypes: ["PRA"], required: true }), documentField("proof_document_id", "Dispatch proof"), confirmation],
  },
  {
    id: "deadline_revision", stage: "bid", title: "Record process deadline revision", submit: "Record revision",
    fields: [record("process_id", "EOI process / RFRP", ["EOI_PROCESS", "RFRP"], { required: true }), field("deadline_key", "Deadline name", "text", { required: true }), field("old_date", "Old date", "date"), field("new_date", "New date", "date", { required: true }), field("reason", "Reason", "textarea", { required: true, full: true }), field("approval_source", "Approval source", "text", { required: true }), field("effective_date", "Effective date", "date", { required: true }), field("coc_meeting_id", "CoC meeting", "meeting"), record("addendum_record_id", "Related addendum", "PROCESS_ADDENDUM")],
  },
  {
    id: "resolution_plan", stage: "plans", title: "Receive Resolution Plan version", submit: "Register plan version",
    note: "A later submission creates V2/V3 and preserves the prior version.",
    fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), record("pra_id", "Final-eligible PRA", "PRA", { required: true }), documentField("document_id", "Resolution Plan document", { required: true }), field("received_at", "Received at", "datetime-local", { required: true, now: true }), field("submission_channel", "Submission channel", "select", { options: options(["EMAIL", "PHYSICAL", "PORTAL", "OTHER"]), default: "EMAIL" }), field("receipt_reference", "Receipt reference"), field("status", "Receipt status", "select", { options: options(["RECEIVED", "UNDER_COMPLIANCE_REVIEW"]), default: "RECEIVED" }), field("remarks", "Remarks", "textarea", { full: true }), field("idempotency_key", "Unique plan receipt reference", "text", { autoKey: "plan-receipt", required: true })],
  },
  {
    id: "section30_review", stage: "plans", title: "Start Section 30(2) review", submit: "Create compliance checklist",
    fields: [record("plan_id", "Exact Plan version", "RESOLUTION_PLAN", { required: true, statuses: ["RECEIVED", "UNDER_COMPLIANCE_REVIEW"] })],
    repeaters: [{ key: "items", label: "Section 30(2) checklist", addLabel: "Add compliance item", fields: [field("category", "Category / clause", "text", { required: true }), field("response", "Response", "select", { options: options(["COMPLIANT", "NON_COMPLIANT", "MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED", "NOT_APPLICABLE"]), default: "MORE_INFORMATION_REQUIRED" }), field("plan_page", "Plan page / clause"), field("reviewer_note", "Reviewer note")] }],
  },
  {
    id: "section30_finalize", stage: "plans", title: "Finalize Section 30(2) review", submit: "Record conclusion", confirm: "Record this professional Section 30(2) conclusion and freeze the checklist?",
    fields: [record("review_id", "Section 30(2) review", "SECTION30_REVIEW", { required: true, mutableOnly: true }), field("status", "Conclusion", "select", { options: options(["COMPLIANT", "NON_COMPLIANT", "MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED"]), required: true }), field("professional_note", "Professional/legal note", "textarea", { required: true, full: true }), confirmation],
  },
  {
    id: "final_eligibility_recheck", stage: "plans", title: "Start final Section 29A recheck", submit: "Start fresh recheck",
    fields: [record("plan_id", "Exact Plan version", "RESOLUTION_PLAN", { required: true }), field("review_stage", "Review stage", "select", { options: options(["PLAN_SUBMISSION_RECHECK", "PRE_COC_VOTE_RECHECK"]), default: "PLAN_SUBMISSION_RECHECK" }), field("legal_reviewer", "Legal reviewer"), field("professional_note", "New information / review note", "textarea", { full: true })],
    repeaters: [{ key: "checklist", label: "Fresh Section 29A checklist", addLabel: "Add recheck item", fields: [field("section", "Section / reference", "text", { required: true }), field("question", "Question", "text", { required: true }), field("response", "Response", "select", { options: options(["YES", "NO", "UNKNOWN", "NOT_APPLICABLE"]), default: "UNKNOWN" }), field("legal_review_required", "Legal review required", "checkbox"), field("reviewer_note", "Reviewer note")] }],
  },
  { id: "plan_query", stage: "plans", title: "Raise Plan deviation / query", submit: "Raise query", fields: [record("plan_id", "Exact Plan version", "RESOLUTION_PLAN", { required: true }), field("category", "Category", "select", { options: options(["RFRP_DEVIATION", "MISSING_INFORMATION", "SECTION_30", "SECTION_29A", "FINANCIAL", "IMPLEMENTATION", "SECURITY", "OTHER"]), default: "RFRP_DEVIATION" }), field("query", "Query / deviation", "textarea", { required: true, full: true }), field("plan_reference", "Plan page / clause"), field("due_date", "Response due date", "date"), documentField("document_id", "Query document"), field("idempotency_key", "Control reference", "text", { autoKey: "plan-query" })] },
  { id: "plan_query_respond", stage: "plans", title: "Record Plan clarification", submit: "Record response", fields: [record("query_id", "Open Plan query", "PLAN_REVIEW_QUERY", { required: true, mutableOnly: true }), field("response", "PRA response / clarification", "textarea", { required: true, full: true }), field("response_date", "Response date", "date"), documentField("response_document_id", "Response document")] },
  {
    id: "evaluate_plan", stage: "decision", title: "Evaluate compliant Plan", submit: "Calculate and lock score", confirm: "Calculate and permanently lock this score against the selected Matrix and Plan versions?",
    fields: [record("plan_id", "Compliant Plan version", "RESOLUTION_PLAN", { required: true, statuses: ["COMPLIANT"] }), record("matrix_id", "Approved Matrix", "EVALUATION_MATRIX", { required: true, statuses: ["APPROVED", "ISSUED"] }), field("rank", "Rank", "number", { min: 1 })],
    repeaters: [{ key: "scores", label: "Criterion scores", addLabel: "Add score", fields: [field("criterion_id", "Matrix criterion", "matrixCriterion", { required: true }), field("value", "Formula input / score", "number", { step: "0.01" }), field("score", "Manual score", "number", { step: "0.01" }), field("authorized_scorer", "Authorized scorer"), field("reason", "Manual scoring reason")] }],
  },
  { id: "negotiation", stage: "decision", title: "Configure negotiation / challenge", submit: "Create approved process", fields: [record("process_id", "EOI process", "EOI_PROCESS", { required: true }), field("negotiation_type", "Process type", "select", { options: options(["DIRECT_NEGOTIATION", "CHALLENGE_MECHANISM", "REVISED_PLAN_ROUND", "SEALED_REVISION", "OTHER"]), default: "REVISED_PLAN_ROUND" }), field("approved_rules", "CoC-approved rules", "textarea", { required: true, full: true }), field("coc_meeting_id", "CoC meeting", "meeting"), confirmation] },
  { id: "negotiation_round", stage: "decision", title: "Open negotiation round", submit: "Open round", fields: [record("negotiation_id", "Approved negotiation process", "NEGOTIATION_PROCESS", { required: true }), field("started_at", "Started at", "datetime-local", { now: true }), field("deadline", "Round deadline", "datetime-local"), field("instructions", "Round instructions", "textarea", { full: true })] },
  { id: "negotiation_submission", stage: "decision", title: "Register round submission", submit: "Register submission", fields: [record("round_id", "Open round", "NEGOTIATION_ROUND", { required: true, statuses: ["OPEN"] }), record("plan_id", "Revised Plan version", "RESOLUTION_PLAN", { required: true }), field("submitted_at", "Submitted at", "datetime-local", { now: true }), field("offer_summary", "Offer / revision summary", "textarea", { full: true }), documentField("document_id", "Submission evidence")] },
  { id: "negotiation_round_close", stage: "decision", title: "Close negotiation round", submit: "Close and freeze round", confirm: "Close and freeze this negotiation round and its chronology?", fields: [record("round_id", "Open round", "NEGOTIATION_ROUND", { required: true, statuses: ["OPEN"] }), field("closed_at", "Closed at", "datetime-local", { now: true }), field("closing_note", "Closing note", "textarea", { full: true })] },
  {
    id: "place_before_coc", stage: "decision", title: "Place Plan before CoC", submit: "Add exact Plan to agenda",
    note: "Create the CoC meeting and agenda version in the CoC module first.",
    fields: [record("plan_id", "Compliant Plan version", "RESOLUTION_PLAN", { required: true, statuses: ["COMPLIANT"] }), field("meeting_id", "CoC meeting", "meeting", { required: true }), field("agenda_version_id", "Agenda version", "agenda", { required: true }), field("title", "Agenda title"), field("agenda_note", "Agenda note", "textarea", { full: true }), field("proposed_resolution_text", "Proposed resolution", "textarea", { required: true, full: true }), field("idempotency_key", "Control reference", "text", { autoKey: "coc-placement" })],
  },
  {
    id: "link_plan_vote", stage: "decision", title: "Link finalized CoC vote", submit: "Link vote to Plan",
    note: "Select the finalized result from the existing CoC voting workflow. Only votes for the selected Plan's agenda placement are shown.",
    fields: [record("plan_id", "Exact voted Plan version", "RESOLUTION_PLAN", { required: true }), field("voting_result_id", "Finalized CoC voting result", "votingResult", { required: true }), field("outcome", "Outcome", "select", { options: options(["APPROVED", "REJECTED", "PENDING"]), default: "APPROVED" }), field("idempotency_key", "Control reference", "text", { autoKey: "plan-vote" })],
  },
  {
    id: "successful_ra", stage: "decision", title: "Record Successful RA", submit: "Confirm selection", confirm: "Confirm the Successful Resolution Applicant for this exact approved Plan version?",
    fields: [record("plan_id", "Approved Plan version", "RESOLUTION_PLAN", { required: true }), record("plan_vote_link_id", "Approved vote link", "PLAN_VOTE_LINK", { required: true, statuses: ["APPROVED", "PASSED"] }), field("selection_date", "Selection date", "date"), field("performance_security_required", "Performance security required", "checkbox", { default: true }), field("selection_note", "Selection note", "textarea", { full: true }), confirmation],
  },
  {
    id: "performance_security_verify", stage: "decision", title: "Verify Successful RA security", submit: "Mark SRA ready for NCLT",
    fields: [record("sra_id", "Successful RA", "SUCCESSFUL_RA", { required: true, statuses: ["PERFORMANCE_SECURITY_PENDING"] }), record("deposit_id", "Verified performance security", "PROCESS_DEPOSIT", { required: true, statuses: ["VERIFIED"], dataFilter: item => item.data?.deposit_type === "PERFORMANCE_SECURITY" })],
  },
  {
    id: "plan_approval", stage: "nclt", title: "Create Plan Approval workspace", submit: "Create/file application",
    note: "The formal pleading and compliance-certificate templates remain TEMPLATE_REQUIRED.",
    confirm: "Create the NCLT Plan Approval application from the selected immutable records?",
    fields: [record("sra_id", "Successful RA ready for NCLT", "SUCCESSFUL_RA", { required: true, statuses: ["READY_FOR_NCLT"] }), record("section30_review_id", "Compliant Section 30(2) review", "SECTION30_REVIEW", { required: true, statuses: ["COMPLIANT"] }), record("final_eligibility_review_id", "Final eligible Section 29A review", "ELIGIBILITY_REVIEW", { required: true, statuses: ["ELIGIBLE"] }), field("application_number", "Application / IA number"), field("filing_date", "Filing date", "date"), field("parties", "Parties"), field("relief_sought", "Relief sought", "textarea", { full: true, default: "Approval of the CoC-approved Resolution Plan" }), documentField("final_application_document_id", "Uploaded final application"), documentField("compliance_certificate_document_id", "Compliance certificate"), field("hearing_at", "Next hearing", "datetime-local"), field("bench", "NCLT bench", "text"), field("hearing_purpose", "Hearing purpose", "text", { default: "Resolution Plan approval" }), field("idempotency_key", "Filing control reference", "text", { autoKey: "plan-approval", required: true })],
  },
  {
    id: "plan_approval_order", stage: "nclt", title: "Record Plan Approval order", submit: "Record NCLT outcome",
    note: "This records the order only. CIRP-106 implementation handover is not started.",
    confirm: "Record this NCLT outcome against the Plan Approval application?",
    fields: [record("workspace_id", "Plan Approval workspace", "PLAN_APPROVAL_WORKSPACE", { required: true }), field("outcome", "Outcome", "select", { options: options(["NCLT_APPROVED", "NCLT_REJECTED", "PENDING"]), default: "PENDING" }), field("order_date", "Order date", "date"), documentField("document_id", "NCLT order document"), field("remarks", "Order / hearing note", "textarea", { full: true })],
  },
];

const nowLocal = () => {
  const value = new Date();
  value.setMinutes(value.getMinutes() - value.getTimezoneOffset());
  return value.toISOString().slice(0, 16);
};

const makeKey = prefix => `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

function initialFieldValue(spec) {
  if (spec.default !== undefined) return spec.default;
  if (spec.autoKey) return makeKey(spec.autoKey);
  if (spec.now) return nowLocal();
  if (spec.type === "checkbox") return false;
  if (spec.type === "multiRecord") return [];
  return "";
}

function emptyRow(repeater) {
  return Object.fromEntries(repeater.fields.map(spec => [spec.key, initialFieldValue(spec)]));
}

function initialForm(config) {
  const values = Object.fromEntries((config.fields || []).map(spec => [spec.key, initialFieldValue(spec)]));
  for (const repeater of config.repeaters || []) values[repeater.key] = [emptyRow(repeater)];
  return values;
}

export function cleanResolutionPayload(value) {
  if (Array.isArray(value)) return value.map(cleanResolutionPayload).filter(item => item !== undefined);
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, child] of Object.entries(value)) {
      const cleaned = cleanResolutionPayload(child);
      if (cleaned !== undefined && cleaned !== "") result[key] = cleaned;
    }
    return result;
  }
  return value === "" || value === null || value === undefined ? undefined : value;
}

function preparePayload(config, form) {
  const payload = { ...form };
  for (const spec of config.fields || []) {
    if (spec.split && typeof payload[spec.key] === "string") {
      payload[spec.key] = payload[spec.key].split(",").map(item => item.trim()).filter(Boolean);
    }
  }
  if (config.id === "evaluation_matrix") {
    payload.criteria = (payload.criteria || []).map(item => ({ ...item, formula: { mode: item.formula_mode || "DIRECT" } }));
    payload.criteria.forEach(item => { delete item.formula_mode; });
  }
  return cleanResolutionPayload(payload);
}

function recordLabel(item) {
  const data = item.data || {};
  const name = data.legal_name || data.member_legal_name || data.title || data.subject || data.receipt_reference || data.application_number || "";
  const version = Number(item.version_number) > 0 ? `V${item.version_number}` : "";
  return [item.record_type?.replaceAll("_", " "), name, version, item.status].filter(Boolean).join(" · ");
}

function phase4Label(item) {
  const data = item.data || {};
  return [item.record_type?.replaceAll("_", " "), data.version_label || data.recipient_name || item.record_key, item.status].filter(Boolean).join(" · ");
}

function meetingLabel(number) {
  const value = Number(number);
  if (!Number.isFinite(value)) return "CoC meeting";
  const mod100 = value % 100;
  const suffix = mod100 >= 11 && mod100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[value % 10] || "th");
  return `${String(value).padStart(2, "0")}${suffix} CoC Meeting`;
}

export function finalizedVotingResults(resources, registers, planId = "") {
  const placements = (registers.COC_PLAN_PLACEMENT || []).filter(item => !planId || item.plan_id === planId);
  const agendaItemIds = new Set(placements.map(item => item.data?.agenda_item_id).filter(Boolean));
  return resources.votingResults.filter(item => {
    if (String(item.status).toUpperCase() !== "FINAL") return false;
    const resolution = resources.resolutions.find(row => row.id === item.resolution_id);
    return !planId || Boolean(resolution && agendaItemIds.has(resolution.agenda_item_id));
  });
}

function matchingRecords(spec, registers) {
  const rows = (spec.recordTypes || []).flatMap(type => registers[type] || []);
  return rows.filter(item => {
    if (spec.statuses && !spec.statuses.includes(String(item.status).toUpperCase())) return false;
    if (spec.mutableOnly && item.immutable_at) return false;
    return spec.dataFilter ? spec.dataFilter(item) : true;
  });
}

function FieldControl({ spec, value, onChange, registers, resources, formContext = {} }) {
  const common = { required: spec.required, value: value ?? "", onChange: event => onChange(event.target.value) };
  if (spec.type === "textarea") return <textarea {...common} placeholder={spec.placeholder} />;
  if (spec.type === "checkbox") return <span className={`resolution-checkbox ${spec.caution ? "caution" : ""}`}><input type="checkbox" required={spec.required} checked={Boolean(value)} onChange={event => onChange(event.target.checked)} /><span>{spec.caution ? "This is a human decision—not a software conclusion." : "Yes"}</span></span>;
  if (spec.type === "select") return <select {...common}>{(spec.options || []).map(item => <option key={item.value} value={item.value}>{item.label || "Select"}</option>)}</select>;
  if (spec.type === "record") {
    const rows = matchingRecords(spec, registers);
    return <select {...common}><option value="">Select record</option>{rows.map(item => <option key={item.id} value={item.id}>{recordLabel(item)}</option>)}</select>;
  }
  if (spec.type === "multiRecord") {
    const rows = matchingRecords(spec, registers);
    return <select multiple required={spec.required} value={value || []} onChange={event => onChange(Array.from(event.target.selectedOptions, item => item.value))}>{rows.map(item => <option key={item.id} value={item.id}>{recordLabel(item)}</option>)}</select>;
  }
  if (spec.type === "document") return <select {...common}><option value="">Select uploaded document</option>{resources.documents.map(item => <option key={item.id} value={item.id}>{item.name || item.title || item.id} · {item.status || "recorded"}</option>)}</select>;
  if (spec.type === "phase4") {
    const rows = (resources.phase4[spec.domain] || []).filter(item => (!spec.recordType || item.record_type === spec.recordType) && (!spec.statuses || spec.statuses.includes(String(item.status).toUpperCase())));
    return <select {...common}><option value="">Select existing record</option>{rows.map(item => <option key={item.id} value={item.id}>{phase4Label(item)}</option>)}</select>;
  }
  if (spec.type === "meeting") return <select {...common}><option value="">Select CoC meeting</option>{resources.meetings.map(item => <option key={item.id} value={item.id}>Meeting {item.meeting_number} · {item.scheduled_start_at || item.meeting_at || item.status}</option>)}</select>;
  if (spec.type === "cocResolution") {
    const rows = resources.resolutions.filter(item => !formContext.coc_meeting_id || item.meeting_id === formContext.coc_meeting_id);
    return <select {...common}><option value="">Select CoC resolution</option>{rows.map(item => <option key={item.id} value={item.id}>{meetingLabel(item.meeting_number)} · {item.title || item.resolution_number} · {item.status}</option>)}</select>;
  }
  if (spec.type === "votingResult") {
    const rows = finalizedVotingResults(resources, registers, formContext.plan_id);
    return <select {...common}><option value="">Select finalized voting result</option>{rows.map(item => {
      const resolution = resources.resolutions.find(row => row.id === item.resolution_id);
      const finalized = (item.finalized_at || item.calculated_at || "").slice(0, 10) || "date not recorded";
      return <option key={item.id} value={item.id}>{meetingLabel(item.meeting_number)} · {resolution?.title || "Resolution vote"} · {item.result} · Finalized {finalized}</option>;
    })}</select>;
  }
  if (spec.type === "agenda") {
    const rows = resources.agendas.filter(item => !formContext.meeting_id || item.meeting_id === formContext.meeting_id);
    return <select {...common}><option value="">Select agenda version</option>{rows.map(item => <option key={item.id} value={item.id}>Meeting {item.meeting_number} · Agenda V{item.version_number} · {item.status}</option>)}</select>;
  }
  if (spec.type === "matrixCriterion") {
    const items = (registers.EVALUATION_MATRIX || []).filter(matrix => !formContext.matrix_id || matrix.id === formContext.matrix_id).flatMap(matrix => (matrix.items || []).map(item => ({ ...item, matrix })));
    return <select {...common}><option value="">Select criterion</option>{items.map(item => <option key={`${item.matrix.id}-${item.item_key}`} value={item.item_key}>{item.matrix.data?.title || `Matrix V${item.matrix.version_number}`} · {item.data?.title || item.item_key} · max {item.data?.maximum_score}</option>)}</select>;
  }
  return <input {...common} type={spec.type || "text"} min={spec.min} max={spec.max} step={spec.step} placeholder={spec.placeholder} />;
}

function ActionForm({ config, registers, resources, onRun, busy }) {
  const [form, setForm] = useState(() => initialForm(config));
  useEffect(() => setForm(initialForm(config)), [config]);

  const updateRow = (repeater, index, key, value) => setForm(current => ({ ...current, [repeater.key]: current[repeater.key].map((row, rowIndex) => rowIndex === index ? { ...row, [key]: value } : row) }));
  const removeRow = (repeater, index) => setForm(current => ({ ...current, [repeater.key]: current[repeater.key].filter((_, rowIndex) => rowIndex !== index) }));
  const addRow = repeater => setForm(current => ({ ...current, [repeater.key]: [...current[repeater.key], emptyRow(repeater)] }));

  const submit = event => {
    event.preventDefault();
    if (config.confirm && !window.confirm(config.confirm)) return;
    onRun(config, preparePayload(config, form)).then(success => { if (success) setForm(initialForm(config)); });
  };

  return <form className="resolution-action-form" onSubmit={submit} data-testid={`resolution-action-${config.id}`}>
    <div className="resolution-action-title"><div><p className="kicker">CONTROLLED ACTION</p><h3>{config.title}</h3>{config.note && <p>{config.note}</p>}</div><ArrowRight size={20} /></div>
    <div className="resolution-form-grid">{(config.fields || []).map(spec => <label className={spec.full ? "full" : ""} key={spec.key}><span>{spec.label}{spec.required && " *"}</span><FieldControl spec={spec} value={form[spec.key]} onChange={value => setForm(current => ({ ...current, [spec.key]: value }))} registers={registers} resources={resources} formContext={form} /></label>)}</div>
    {(config.repeaters || []).map(repeater => <section className="resolution-repeater" key={repeater.key}><header><div><b>{repeater.label}</b><small>Each row is retained as structured evidence.</small></div><button type="button" className="outline-button small" onClick={() => addRow(repeater)}>+ {repeater.addLabel}</button></header>{(form[repeater.key] || []).map((row, index) => <div className="resolution-repeater-row" key={`${repeater.key}-${index}`}><span className="row-number">{index + 1}</span>{repeater.fields.map(spec => <label key={spec.key}><span>{spec.label}{spec.required && " *"}</span><FieldControl spec={spec} value={row[spec.key]} onChange={value => updateRow(repeater, index, spec.key, value)} registers={registers} resources={resources} formContext={form} /></label>)}<button type="button" className="text-button danger" disabled={(form[repeater.key] || []).length === 1} onClick={() => removeRow(repeater, index)}>Remove</button></div>)}</section>)}
    <div className="resolution-submit"><span>Required fields are marked *. Backend legal and lifecycle gates remain authoritative.</span><button className="primary-button" disabled={Boolean(busy)}><CheckCircle2 size={16} />{busy ? "Saving…" : config.submit}</button></div>
  </form>;
}

function SummaryBoard({ summary, registers, onStage }) {
  const metrics = [
    ["EOI process", summary.EOI_process_status, "process"], ["EOIs received", summary.EOIs_received || 0, "applicants"],
    ["Final eligible PRAs", summary.final_eligible_PRA_count || 0, "eligibility"], ["RFRP", summary.RFRP_status, "bid"],
    ["Plans received", summary.plans_received || 0, "plans"], ["Compliant plans", summary.compliant_plans || 0, "plans"],
    ["Plans evaluated", summary.plans_evaluated || 0, "decision"], ["Plan vote", summary.plan_vote_status, "decision"],
    ["Performance security", summary.performance_security_status, "decision"], ["NCLT application", summary.plan_approval_application_status, "nclt"],
  ];
  const gates = [
    ["EOI publication", summary.EOI_process_status === "PUBLISHED", "Approve the criteria, upload final EOI and publication proof."],
    ["Final PRA list", (summary.final_eligible_PRA_count || 0) > 0, "Complete Section 29A reviews, objections and issue the final list."],
    ["PRA issue package", (summary.issue_packages_sent || 0) > 0, "Verify confidentiality and select exact RFRP, Matrix and IM versions."],
    ["Plan compliance", (summary.compliant_plans || 0) > 0, "Complete the Section 30(2) checklist and final Section 29A recheck."],
    ["CoC decision", !["NOT_RECORDED", null, undefined].includes(summary.plan_vote_status), "Evaluate the exact plan, place it on the CoC agenda and link the final vote."],
    ["NCLT filing", !["NOT_RECORDED", null, undefined].includes(summary.plan_approval_application_status), "Verify performance security and assemble the approval workspace."],
  ];
  return <div className="space-y-6">
    <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">PHASE 6 CONTROL ROOM</p><h3>EOI → PRA → Plan → CoC → NCLT</h3><p className="module-description">Operational status only. Eligibility, compliance, scoring approval, selection and court approval remain separate decisions.</p></div><Layers3 size={22} /></div><div className="resolution-metrics">{metrics.map(([label, value, stage]) => <button type="button" onClick={() => onStage(stage)} key={label}><span>{label}</span><b>{String(value ?? "NOT RECORDED").replaceAll("_", " ")}</b></button>)}</div></section>
    <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">PROFESSIONAL GATES</p><h3>Readiness sequence</h3></div><ShieldCheck size={21} /></div><div className="resolution-gates">{gates.map(([label, done, detail], index) => <article className={done ? "complete" : "pending"} key={label}><span>{done ? <CheckCircle2 size={18} /> : index + 1}</span><div><b>{label}</b><small>{done ? "Recorded gate satisfied" : detail}</small></div></article>)}</div><div className="resolution-separation"><AlertTriangle size={18} /><span><b>Required separation:</b> PRA eligibility ≠ Plan compliance ≠ Evaluation ≠ CoC selection ≠ NCLT approval.</span></div></section>
    <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">CURRENT RECORD VOLUME</p><h3>Phase 6 register</h3></div><ListChecks size={20} /></div><div className="resolution-type-counts">{REGISTER_TYPES.filter(type => (registers[type] || []).length).map(type => <span key={type}><b>{(registers[type] || []).length}</b>{type.replaceAll("_", " ")}</span>)}{!REGISTER_TYPES.some(type => (registers[type] || []).length) && <p className="panel-empty">No Phase 6 records have been created for this company.</p>}</div></section>
  </div>;
}

function StageRegister({ stage, registers }) {
  const rows = stage.types.flatMap(type => registers[type] || []).sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  return <section className="casefile-panel resolution-register"><div className="panel-heading"><div><p className="kicker">STAGE REGISTER</p><h3>{stage.label} records</h3></div><ListChecks size={20} /></div><div className="module-table-wrap"><table className="module-table"><thead><tr><th>Record</th><th>Identity / version</th><th>Status</th><th>Recorded</th><th>Control</th></tr></thead><tbody>{rows.map(item => <tr key={item.id}><td>{item.record_type.replaceAll("_", " ")}</td><td>{recordLabel(item)}</td><td><span className={`status-chip ${item.immutable_at ? "locked" : ""}`}>{item.status}</span></td><td>{item.created_at?.slice(0, 16).replace("T", " ") || "—"}</td><td><details><summary>Inspect</summary><div className="resolution-record-detail"><code>{item.id}</code><button type="button" className="text-button" onClick={() => navigator.clipboard?.writeText(item.id)}>Copy ID</button><pre>{JSON.stringify({ data: item.data, items: item.items, snapshot: item.snapshot }, null, 2)}</pre></div></details></td></tr>)}{!rows.length && <tr><td className="panel-empty" colSpan="5">No records in this stage yet.</td></tr>}</tbody></table></div></section>;
}

export default function ResolutionProcessWorkspace({ caseRecord }) {
  const caseId = caseRecord.id;
  const [stageId, setStageId] = useState("overview");
  const [selectedAction, setSelectedAction] = useState("");
  const [summary, setSummary] = useState({});
  const [registers, setRegisters] = useState({});
  const [resources, setResources] = useState({ documents: [], meetings: [], agendas: [], resolutions: [], votingResults: [], phase4: { im: [], confidentiality: [], vdr: [] } });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [denied, setDenied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setDenied(false);
    try {
      const [summaryResponse, documentResponse, meetingResponse, ...registerResponses] = await Promise.all([
        api.get(`/cases/${caseId}/resolution-process/summary`), api.get(`/cases/${caseId}/documents`),
        api.get(`/cases/${caseId}/coc/meetings`),
        ...REGISTER_TYPES.map(type => api.get(`/cases/${caseId}/resolution-process/register/${type}`)),
      ]);
      const meetingDetails = await Promise.all((meetingResponse.data || []).map(item => api.get(`/cases/${caseId}/coc/meetings/${item.id}`).then(response => response.data).catch(() => item)));
      const phase4Results = await Promise.all(["im", "confidentiality", "vdr"].map(domain => api.get(`/cases/${caseId}/phase4/${domain}`).then(response => response.data).catch(() => [])));
      const nextRegisters = Object.fromEntries(REGISTER_TYPES.map((type, index) => [type, registerResponses[index].data || []]));
      const detailRequests = REGISTER_TYPES.filter(type => DETAILED_REGISTER_TYPES.has(type)).flatMap(type =>
        nextRegisters[type].map(item => api.get(`/cases/${caseId}/resolution-process/records/${item.id}`).then(response => ({ type, item: response.data }))),
      );
      const detailedRecords = await Promise.all(detailRequests);
      for (const { type, item } of detailedRecords) {
        nextRegisters[type] = nextRegisters[type].map(current => current.id === item.id ? item : current);
      }
      setSummary(summaryResponse.data || {}); setRegisters(nextRegisters);
      setResources({
        documents: documentResponse.data || [], meetings: meetingDetails,
        agendas: meetingDetails.flatMap(meeting => (meeting.agendas || []).map(agenda => ({ ...agenda, meeting_number: meeting.meeting_number }))),
        resolutions: meetingDetails.flatMap(meeting => (meeting.resolutions || []).map(resolution => ({ ...resolution, meeting_number: meeting.meeting_number }))),
        votingResults: meetingDetails.flatMap(meeting => (meeting.voting_results || []).map(result => ({ ...result, meeting_number: meeting.meeting_number }))),
        phase4: { im: phase4Results[0], confidentiality: phase4Results[1], vdr: phase4Results[2] },
      });
    } catch (error) {
      if (error.response?.status === 403) setDenied(true);
      else toast.error(errorMessage(error, "Could not load the Resolution Process workspace."));
    } finally { setLoading(false); }
  }, [caseId]);

  useEffect(() => { load(); }, [load]);

  const stage = STAGES.find(item => item.id === stageId) || STAGES[0];
  const stageActions = useMemo(() => ACTIONS.filter(action => action.stage === stageId), [stageId]);
  const activeConfig = ACTIONS.find(action => action.id === selectedAction && action.stage === stageId) || stageActions[0];

  useEffect(() => { setSelectedAction(""); }, [stageId]);

  const run = async (config, payload) => {
    setBusy(config.id);
    try {
      const { data } = await api.post(`/cases/${caseId}/resolution-process/actions/${config.id}`, payload);
      toast.success(`${config.title} completed${data?.idempotent_replay ? " (existing record reused)" : ""}`);
      await load(); return true;
    } catch (error) { toast.error(errorMessage(error, `Could not complete ${config.title.toLowerCase()}.`)); return false; }
    finally { setBusy(""); }
  };

  if (loading) return <section className="casefile-panel panel-empty">Loading Resolution Process workspace…</section>;
  if (denied) return <section className="casefile-panel resolution-denied"><ShieldCheck size={30} /><h3>Restricted professional workspace</h3><p>PRA, Resolution Plan and evaluation records require administrator, professional or manager access. The backend has denied this account.</p></section>;

  return <div className="resolution-workspace" data-testid="resolution-process-workspace">
    <section className="resolution-banner"><div><p className="kicker">CIRP-085 TO CIRP-105</p><h3>Resolution Process Operator Workspace</h3><p>Case-specific controls for {caseRecord.name}. No AI eligibility, compliance, scoring approval or selection decisions.</p></div><button className="outline-button" type="button" onClick={load}><RefreshCw size={16} />Refresh all registers</button></section>
    <nav className="resolution-stage-nav" aria-label="Resolution Process stages">{STAGES.map(item => { const Icon = item.icon; const count = item.types.reduce((total, type) => total + (registers[type] || []).length, 0); return <button type="button" className={stageId === item.id ? "active" : ""} key={item.id} onClick={() => setStageId(item.id)}><Icon size={18} /><span><b>{item.label}</b><small>{item.subtitle}</small></span>{item.id !== "overview" && <em>{count}</em>}</button>; })}</nav>
    {stageId === "overview" ? <SummaryBoard summary={summary} registers={registers} onStage={setStageId} /> : <div className="resolution-stage-layout"><aside className="resolution-action-menu"><p className="kicker">AVAILABLE ACTIONS</p>{stageActions.map(action => <button type="button" className={activeConfig?.id === action.id ? "active" : ""} key={action.id} onClick={() => setSelectedAction(action.id)}><span>{action.title}</span><ArrowRight size={15} /></button>)}</aside><main>{activeConfig && <ActionForm key={activeConfig.id} config={activeConfig} registers={registers} resources={resources} onRun={run} busy={busy} />}<StageRegister stage={stage} registers={registers} /></main></div>}
  </div>;
}
