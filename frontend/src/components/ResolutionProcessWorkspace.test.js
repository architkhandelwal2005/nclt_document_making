/* global describe, expect, test */
import { cleanResolutionPayload, finalizedVotingResults } from "./ResolutionProcessWorkspace";

describe("Resolution Process payload normalization", () => {
  test("removes blank optional values without removing explicit decisions", () => {
    expect(cleanResolutionPayload({
      title: "Criterion A",
      optional_note: "",
      professional_confirmed: false,
      score: 0,
      nested: { response: "YES", empty: null },
      items: [{ item_key: "A", note: "" }],
    })).toEqual({
      title: "Criterion A",
      professional_confirmed: false,
      score: 0,
      nested: { response: "YES" },
      items: [{ item_key: "A" }],
    });
  });

  test("offers only finalized CoC results for the selected Plan placement", () => {
    const resources = {
      resolutions: [
        { id: "resolution-a", agenda_item_id: "agenda-a" },
        { id: "resolution-b", agenda_item_id: "agenda-b" },
      ],
      votingResults: [
        { id: "draft", resolution_id: "resolution-a", status: "CALCULATED" },
        { id: "right-plan", resolution_id: "resolution-a", status: "FINAL" },
        { id: "other-plan", resolution_id: "resolution-b", status: "FINAL" },
      ],
    };
    const registers = { COC_PLAN_PLACEMENT: [
      { plan_id: "plan-a", data: { agenda_item_id: "agenda-a" } },
      { plan_id: "plan-b", data: { agenda_item_id: "agenda-b" } },
    ] };
    expect(finalizedVotingResults(resources, registers, "plan-a").map(item => item.id)).toEqual(["right-plan"]);
  });
});
