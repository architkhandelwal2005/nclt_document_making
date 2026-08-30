/* global describe, expect, test */
import { cleanResolutionPayload } from "./ResolutionProcessWorkspace";

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
});
