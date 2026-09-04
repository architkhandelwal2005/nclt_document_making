/* global describe, expect, test */
import { resolveApiBase } from "./api";

describe("API origin selection", () => {
  test("production and UAT ignore an absolute development override", () => {
    expect(resolveApiBase({
      mode: "production",
      configuredBackend: "http://127.0.0.1:8001",
    })).toBe("/api");
  });

  test("development may use its explicitly configured backend", () => {
    expect(resolveApiBase({
      mode: "development",
      configuredBackend: "http://127.0.0.1:8001/",
    })).toBe("http://127.0.0.1:8001/api");
  });
});
