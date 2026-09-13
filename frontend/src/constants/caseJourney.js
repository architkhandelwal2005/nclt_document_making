export const JOURNEY_STAGES = [
  { id: "admission", phaseCode: "ADMISSION_TAKEOVER", title: "Admission & Takeover", description: "Confirm the admission record, case master and takeover evidence.", destinations: [["intake", "Admission intake"], ["overview", "Case master"], ["documents", "Admission documents"]] },
  { id: "announcement", phaseCode: "PUBLIC_ANNOUNCEMENT", title: "Public Announcement", description: "Prepare, review and confirm Form A and publication records.", destinations: [["public-announcement", "Public Announcement"]] },
  { id: "claims", phaseCode: "CLAIMS_VERIFICATION", title: "Claims & Verification", description: "Receive, examine and decide creditor claims.", destinations: [["claims", "Claims register"]] },
  { id: "eligibility", phaseCode: "COC_ELIGIBILITY", title: "CoC Eligibility", description: "Confirm creditor eligibility and voting rights.", destinations: [["coc", "CoC workspace"]] },
  { id: "constitution", phaseCode: "COC_CONSTITUTION", title: "CoC Constitution", description: "Constitute or reconstitute the committee of creditors.", destinations: [["coc", "CoC workspace"]] },
  { id: "meetings", phaseCode: "COC_MEETINGS", title: "CoC Meetings & Voting", description: "Manage meetings, notices, minutes and voting.", destinations: [["coc", "CoC meetings & voting"]] },
  { id: "operations", phaseCode: "OPERATIONS_VALUATION_IM", title: "Operations, Valuation, IM & VDR", description: "Run operations controls, valuation, information memorandum and data room.", destinations: [["operations-workspace", "Operations workspace"], ["valuations", "Legacy valuation register"]] },
  { id: "audit", phaseCode: "TRANSACTION_AUDIT_AVOIDANCE", title: "Transaction Audit & Avoidance", description: "Manage audit engagement, findings and avoidance actions.", destinations: [["transaction-audit", "Transaction audit workspace"]] },
  { id: "resolution", phaseCode: "EOI_PRA_RESOLUTION_PLAN", title: "EOI, PRA & Resolution Plan", description: "Operate the EOI through plan evaluation, voting and approval process.", destinations: [["resolution-process", "Resolution Process"]] },
];

export const TERMINAL_STEP_STATUSES = new Set(["COMPLETED", "WAIVED", "CANCELLED"]);
export const ACTIVE_STEP_STATUSES = new Set(["READY", "IN_PROGRESS", "WAITING", "BLOCKED", "PENDING_APPROVAL"]);

const timestamp = value => value ? new Date(value).getTime() : Number.MAX_SAFE_INTEGER;
export const isTerminalStep = step => TERMINAL_STEP_STATUSES.has(step.status);
export const isActionableStep = step => ACTIVE_STEP_STATUSES.has(step.status);

export function buildJourneyStages(steps = []) {
  const byPhase = new Map();
  steps.forEach(step => {
    const list = byPhase.get(step.phase_code) || [];
    list.push(step); byPhase.set(step.phase_code, list);
  });
  let foundCurrent = false;
  return JOURNEY_STAGES.map(stage => {
    const stageSteps = (byPhase.get(stage.phaseCode) || []).sort((a, b) => Number(a.sequence || 0) - Number(b.sequence || 0));
    const terminal = stageSteps.filter(isTerminalStep);
    const active = stageSteps.filter(isActionableStep);
    const blocked = active.filter(step => step.status === "BLOCKED");
    const complete = stageSteps.length > 0 && terminal.length === stageSteps.length;
    let state = "upcoming";
    if (complete) state = "completed";
    else if (!foundCurrent && active.length) { state = blocked.length ? "blocked" : "current"; foundCurrent = true; }
    const dueSteps = stageSteps.filter(step => !isTerminalStep(step) && step.effective_due_date);
    const nextDue = dueSteps.sort((a, b) => timestamp(a.effective_due_date) - timestamp(b.effective_due_date))[0]?.effective_due_date || null;
    return { ...stage, steps: stageSteps, activeSteps: active, blockedSteps: blocked, terminalCount: terminal.length, totalCount: stageSteps.length, complete, state, progress: stageSteps.length ? Math.round((terminal.length / stageSteps.length) * 100) : 0, nextDue };
  });
}

export function currentStageWork(stage) {
  if (!stage || !["current", "blocked"].includes(stage.state)) return [];
  return [...stage.activeSteps].sort((a, b) => timestamp(a.effective_due_date) - timestamp(b.effective_due_date) || Number(a.sequence || 0) - Number(b.sequence || 0));
}
