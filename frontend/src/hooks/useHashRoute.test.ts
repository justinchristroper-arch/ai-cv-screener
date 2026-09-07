import { describe, expect, it } from "vitest";

import { href, parseHash } from "./useHashRoute";

describe("parseHash", () => {
  it("treats an empty hash as the jobs list", () => {
    expect(parseHash("")).toEqual({ name: "jobs" });
    expect(parseHash("#")).toEqual({ name: "jobs" });
    expect(parseHash("#/")).toEqual({ name: "jobs" });
    expect(parseHash("#/jobs")).toEqual({ name: "jobs" });
  });

  it("parses a job route", () => {
    expect(parseHash("#/jobs/abc-123")).toEqual({ name: "job", jobId: "abc-123" });
  });

  it("parses a candidate route", () => {
    expect(parseHash("#/candidates/xyz-9")).toEqual({ name: "candidate", candidateId: "xyz-9" });
  });

  it("tolerates a trailing slash", () => {
    expect(parseHash("#/jobs/abc-123/")).toEqual({ name: "job", jobId: "abc-123" });
  });

  it("reports anything else as unknown rather than guessing", () => {
    expect(parseHash("#/nope")).toEqual({ name: "unknown", path: "nope" });
    expect(parseHash("#/jobs/a/b/c")).toEqual({ name: "unknown", path: "jobs/a/b/c" });
  });

  it("round-trips the hrefs it builds", () => {
    expect(parseHash(href.jobs())).toEqual({ name: "jobs" });
    expect(parseHash(href.job("j1"))).toEqual({ name: "job", jobId: "j1" });
    expect(parseHash(href.candidate("c1"))).toEqual({ name: "candidate", candidateId: "c1" });
  });
});
