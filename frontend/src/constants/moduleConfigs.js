import { field } from "../components/CaseModuleManager";

export const taskConfig = {
  module: "tasks", title: "Tasks", description: "Assignments, ownership, dependencies, and due dates.",
  fields: [field("title", "Task title", "text", { required: true, full: true }), field("description", "Description", "textarea", { full: true }), field("category", "Category"), field("assignee_id", "Assignee"), field("priority", "Priority", "select", { default: "normal", options: ["low", "normal", "high", "urgent"] }), field("status", "Status", "select", { default: "open", options: ["open", "in-progress", "blocked", "completed", "cancelled"] }), field("start_date", "Start date", "date"), field("due_date", "Due date", "date")],
  columns: [{ key: "title", label: "Task" }, { key: "category", label: "Category" }, { key: "priority", label: "Priority" }, { key: "due_date", label: "Due" }],
};

export const deadlineConfig = {
  module: "deadlines", title: "Compliance deadlines", description: "Calculated dates, overrides, completion, and evidence tracking.",
  fields: [field("title", "Obligation", "text", { required: true, full: true }), field("provision", "Provision"), field("due_date", "Due date", "date", { required: true }), field("responsible_user_id", "Responsible person"), field("status", "Status", "select", { default: "open", options: ["open", "in-progress", "completed", "not-applicable"] }), field("override_reason", "Override / exception reason", "textarea", { full: true })],
  columns: [{ key: "title", label: "Obligation" }, { key: "provision", label: "Provision" }, { key: "due_date", label: "Due" }, { key: "override_reason", label: "Override" }],
};

export const hearingConfig = {
  module: "hearings", title: "Hearings", description: "Listings, appearances, next dates, and hearing notes.",
  fields: [field("hearing_at", "Hearing date and time", "datetime-local", { required: true, now: true }), field("tribunal", "Tribunal", "select", { default: "NCLT", options: ["NCLT", "NCLAT", "Supreme Court", "High Court", "Other"] }), field("bench", "Bench"), field("purpose", "Purpose", "text", { required: true, full: true }), field("counsel", "Counsel"), field("venue_or_link", "Court hall / meeting link"), field("status", "Status", "select", { default: "scheduled", options: ["scheduled", "heard", "adjourned", "disposed", "cancelled"] }), field("next_hearing_at", "Next hearing", "datetime-local"), field("notes", "Hearing notes", "textarea", { full: true })],
  columns: [{ key: "hearing_at", label: "Date" }, { key: "purpose", label: "Purpose" }, { key: "bench", label: "Bench" }, { key: "next_hearing_at", label: "Next date" }],
};

export const applicationConfig = {
  module: "applications", title: "Applications", description: "Filings, defects, relief sought, and disposal.",
  fields: [field("application_type", "Application type", "text", { required: true }), field("number", "Diary / application number"), field("filing_date", "Filing date", "date"), field("parties", "Parties", "text", { full: true }), field("relief_sought", "Relief sought", "textarea", { full: true }), field("status", "Status", "select", { default: "draft", options: ["draft", "filed", "defective", "listed", "allowed", "dismissed", "disposed"] }), field("defects", "Filing defects", "textarea", { full: true }), field("disposal_date", "Disposal date", "date")],
  columns: [{ key: "application_type", label: "Type" }, { key: "number", label: "Number" }, { key: "filing_date", label: "Filed" }, { key: "relief_sought", label: "Relief" }],
};

export const claimConfig = {
  module: "claims", title: "Claims", description: "Submission, deficiency, verification, admission, and revision history.",
  fields: [field("creditor_category", "Creditor category", "select", { required: true, options: ["Financial creditor", "Operational creditor", "Employee / workman", "Government dues", "Other"] }), field("form_type", "Claim form"), field("received_date", "Received date", "date", { today: true }), field("claimed_amount", "Claimed amount", "number", { step: "0.01" }), field("admitted_amount", "Admitted amount", "number", { step: "0.01" }), field("status", "Status", "select", { default: "received", options: ["received", "under_review", "deficient", "admitted", "partially_admitted", "rejected", "withdrawn"] }), field("security_details", "Security details", "textarea", { full: true }), field("deficiency_notes", "Deficiencies", "textarea", { full: true }), field("decision_reason", "Decision reason", "textarea", { full: true })],
  columns: [{ key: "creditor_category", label: "Category" }, { key: "received_date", label: "Received" }, { key: "claimed_amount", label: "Claimed" }, { key: "admitted_amount", label: "Admitted" }],
};

export const cocMemberConfig = {
  module: "coc-members", title: "CoC membership", description: "Historically effective admitted debt and voting shares.",
  fields: [field("admitted_debt", "Admitted debt", "number", { step: "0.01", required: true }), field("voting_share", "Voting share (%)", "number", { step: "0.0001", required: true }), field("valid_from", "Effective from", "date", { required: true, today: true }), field("valid_to", "Effective to", "date"), field("authorized_representative", "Authorized representative", "text", { full: true })],
  columns: [{ key: "authorized_representative", label: "Representative" }, { key: "admitted_debt", label: "Admitted debt" }, { key: "voting_share", label: "Voting share" }, { key: "valid_from", label: "From" }],
};

export const cocMeetingConfig = {
  module: "coc-meetings", title: "CoC meetings", description: "Notices, meeting details, voting windows, agenda, and minutes.",
  fields: [field("meeting_number", "Meeting number", "number", { required: true }), field("meeting_at", "Scheduled date and time", "datetime-local", { required: true, now: true }), field("actual_start_at", "Actual start", "datetime-local"), field("actual_end_at", "Actual end", "datetime-local"), field("mode", "Mode", "select", { options: ["Physical", "Video conference", "Hybrid"] }), field("venue_or_link", "Venue / meeting link", "text", { full: true }), field("notice_date", "Notice date", "date"), field("notice_place", "Notice place"), field("voting_start", "Voting starts", "datetime-local"), field("voting_end", "Voting ends", "datetime-local"), field("quorum_threshold", "Quorum threshold (%)", "number", { default: 33, step: "0.0001" }), field("chair_name", "Chair / RP name"), field("status", "Status", "select", { default: "planned", options: ["planned", "notice-issued", "held", "voting-open", "completed", "cancelled"] })],
  columns: [{ key: "meeting_number", label: "Meeting" }, { key: "meeting_at", label: "Date" }, { key: "mode", label: "Mode" }, { key: "voting_end", label: "Voting ends" }],
};

export const communicationConfig = {
  module: "communications", title: "Communications", description: "Email, letter, courier, WhatsApp, portal, telephone, and meeting history.",
  fields: [field("channel", "Channel", "select", { required: true, options: ["Email", "Physical letter", "Courier", "WhatsApp", "Portal", "Telephone", "Meeting"] }), field("direction", "Direction", "select", { default: "outgoing", options: ["outgoing", "incoming", "internal"] }), field("occurred_at", "Date and time", "datetime-local", { required: true, now: true }), field("sender", "Sender"), field("recipients", "Recipients", "text", { full: true }), field("subject", "Subject", "text", { full: true }), field("summary", "Content / summary", "textarea", { full: true }), field("delivery_status", "Delivery status")],
  columns: [{ key: "occurred_at", label: "Date" }, { key: "channel", label: "Channel" }, { key: "subject", label: "Subject" }, { key: "recipients", label: "Recipients" }],
};

export const assetConfig = {
  module: "assets", title: "Assets", description: "Ownership, possession, security, insurance, and current condition.",
  fields: [field("category", "Asset category", "text", { required: true }), field("description", "Description", "textarea", { required: true, full: true }), field("ownership", "Ownership"), field("location", "Location"), field("book_value", "Book value", "number", { step: "0.01" }), field("security_interest", "Security interest"), field("possession", "Possession"), field("insurance", "Insurance"), field("encumbrance", "Encumbrance", "textarea", { full: true }), field("status", "Status", "select", { default: "identified", options: ["identified", "verified", "secured", "under_valuation", "sold", "disputed"] })],
  columns: [{ key: "category", label: "Category" }, { key: "description", label: "Asset" }, { key: "book_value", label: "Book value" }, { key: "location", label: "Location" }],
};

export const financialConfig = {
  module: "financial-records", title: "Financial information", description: "Books, accounts, receivables, creditors, dues, and transactions.",
  fields: [field("record_type", "Record type", "select", { required: true, options: ["Bank account", "Book creditor", "Receivable", "Statutory due", "Employee", "Contract", "Transaction review", "Other"] }), field("name", "Name / description", "text", { required: true }), field("amount", "Amount", "number", { step: "0.01" }), field("as_of_date", "As-of date", "date")],
  columns: [{ key: "record_type", label: "Type" }, { key: "name", label: "Record" }, { key: "amount", label: "Amount" }, { key: "as_of_date", label: "As of" }],
};

export const valuationConfig = {
  module: "valuations", title: "Valuation", description: "Appointments, inspections, reports, fair value, and liquidation value.",
  fields: [field("asset_class", "Asset class", "text", { required: true }), field("appointment_date", "Appointment date", "date"), field("inspection_date", "Inspection date", "date"), field("report_date", "Report date", "date"), field("fair_value", "Fair value", "number", { step: "0.01" }), field("liquidation_value", "Liquidation value", "number", { step: "0.01" }), field("status", "Status", "select", { default: "appointed", options: ["appointed", "information-pending", "inspection-complete", "report-received", "finalized"] }), field("confidential", "Confidential", "checkbox", { default: true }), field("notes", "Notes", "textarea", { full: true })],
  columns: [{ key: "asset_class", label: "Asset class" }, { key: "report_date", label: "Report" }, { key: "fair_value", label: "Fair value" }, { key: "liquidation_value", label: "Liquidation value" }],
};

export const expenseConfig = {
  module: "expenses", title: "Expenses", description: "Invoices, approval, payment, and CIRP cost eligibility.",
  fields: [field("category", "Category", "text", { required: true }), field("invoice_number", "Invoice number"), field("expense_date", "Expense date", "date", { required: true, today: true }), field("amount", "Amount", "number", { step: "0.01", required: true }), field("tax_amount", "Tax", "number", { step: "0.01" }), field("approval_status", "Approval", "select", { default: "pending", options: ["pending", "approved", "rejected"] }), field("payment_status", "Payment", "select", { default: "unpaid", options: ["unpaid", "part-paid", "paid"] }), field("cirp_cost_eligible", "CIRP cost eligible", "checkbox")],
  columns: [{ key: "expense_date", label: "Date" }, { key: "category", label: "Category" }, { key: "invoice_number", label: "Invoice" }, { key: "amount", label: "Amount" }],
};

export const contributionConfig = {
  module: "contributions", title: "CoC contributions", description: "Calls, receipts, balances, and reminders.",
  fields: [field("called_amount", "Called amount", "number", { step: "0.01", required: true }), field("due_date", "Due date", "date"), field("paid_amount", "Paid amount", "number", { step: "0.01" }), field("paid_date", "Paid date", "date"), field("notes", "Notes", "textarea", { full: true })],
  columns: [{ key: "called_amount", label: "Called" }, { key: "due_date", label: "Due" }, { key: "paid_amount", label: "Paid" }, { key: "paid_date", label: "Paid on" }],
};
