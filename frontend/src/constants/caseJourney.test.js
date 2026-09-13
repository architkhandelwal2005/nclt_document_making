import { buildJourneyStages, currentStageWork } from "./caseJourney";

const step = (phase_code, status, extras = {}) => ({ id: `${phase_code}-${status}-${extras.sequence || 0}`, phase_code, status, sequence: extras.sequence || 0, ...extras });

describe("Case Journey stage roll-up", () => {
  it("uses versioned steps for completed, current, blocked and upcoming stages", () => {
    const stages = buildJourneyStages([
      step("ADMISSION_TAKEOVER", "COMPLETED"),
      step("PUBLIC_ANNOUNCEMENT", "READY", { effective_due_date: "2026-09-18" }),
      step("CLAIMS_VERIFICATION", "BLOCKED", { sequence: 1 }),
    ]);
    expect(stages[0]).toMatchObject({ state: "completed", progress: 100 });
    expect(stages[1]).toMatchObject({ state: "current", progress: 0, nextDue: "2026-09-18" });
    expect(stages[2]).toMatchObject({ state: "upcoming" });
    expect(stages[3]).toMatchObject({ state: "upcoming" });
  });

  it("shows only actionable work in the current stage", () => {
    const stages = buildJourneyStages([
      step("ADMISSION_TAKEOVER", "READY", { sequence: 2, task_title: "Linked intake task" }),
      step("ADMISSION_TAKEOVER", "COMPLETED", { sequence: 1 }),
      step("PUBLIC_ANNOUNCEMENT", "READY", { sequence: 3, task_title: "Future task" }),
    ]);
    const work = currentStageWork(stages[0]);
    expect(work).toHaveLength(1);
    expect(work[0].task_title).toBe("Linked intake task");
    expect(currentStageWork(stages[1])).toEqual([]);
  });
});
