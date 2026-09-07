/**
 * Wording is a correctness concern in this product, so it is tested like one.
 */

import { describe, expect, it } from "vitest";

import {
  BAND_CAVEAT,
  BAND_LABEL,
  SCORE_CAVEAT,
  VERDICT_LABEL,
  VERDICT_MEANING,
  asPercent,
  failureLabel,
  describeError,
  isDemoLimitation,
  trimDecimal,
  warningLabel,
} from "./display";

describe("verdict wording", () => {
  it("reports absence as absence of evidence in the document", () => {
    expect(VERDICT_LABEL.NO_EVIDENCE).toBe("No evidence found in CV");
    expect(VERDICT_MEANING.NO_EVIDENCE).toMatch(/about the document, not about the candidate/i);
  });

  it("never claims a candidate lacks something", () => {
    const everything = [
      ...Object.values(VERDICT_LABEL),
      ...Object.values(VERDICT_MEANING),
      ...Object.values(BAND_LABEL),
      BAND_CAVEAT,
      SCORE_CAVEAT,
    ].join(" ");

    expect(everything).not.toMatch(/does not have/i);
    expect(everything).not.toMatch(/\blacks\b/i);
    expect(everything).not.toMatch(/unqualified/i);
  });
});

describe("band wording", () => {
  it("says a band is heuristic, not a decision or a probability", () => {
    expect(BAND_CAVEAT).toMatch(/heuristic/i);
    expect(BAND_CAVEAT).toMatch(/not a hiring decision/i);
    expect(BAND_CAVEAT).toMatch(/not a probability/i);
    expect(BAND_CAVEAT).toMatch(/no candidate is filtered or hidden/i);
  });

  it("warns that scores are only comparable within one job", () => {
    expect(SCORE_CAVEAT).toMatch(/only comparable within this job/i);
  });
});

describe("failure and warning wording", () => {
  it("explains a missing text layer without blaming the candidate", () => {
    expect(failureLabel("NO_TEXT_LAYER")).toMatch(/does not perform OCR/i);
  });

  it("falls back to the raw code rather than inventing a label", () => {
    expect(failureLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(warningLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(failureLabel(null)).toBe("Processing failed");
  });

  it("explains the must-have cap as a cap on the band, not on the candidate", () => {
    expect(warningLabel("MUST_HAVE_NOT_EVIDENCED")).toMatch(/band is capped at review/i);
  });
});

describe("number formatting", () => {
  it("renders a decimal coverage string as a percentage", () => {
    expect(asPercent("0.88888889")).toBe("89%");
    expect(asPercent("1.0")).toBe("100%");
    expect(asPercent("0")).toBe("0%");
  });

  it("renders a missing coverage as unknown rather than as zero", () => {
    expect(asPercent(null)).toBe("—");
  });

  it("trims trailing zeros so a weight reads as a person would write it", () => {
    expect(trimDecimal("3.00")).toBe("3");
    expect(trimDecimal("1.50")).toBe("1.5");
    expect(trimDecimal("0.00")).toBe("0");
    expect(trimDecimal("31")).toBe("31");
  });
});

describe("error wording", () => {
  it("explains the demo-mode limitation without technical jargon", () => {
    const message = describeError(
      Object.assign(new Error("No recorded fixture for this input in demo mode."), {
        code: "llm_unavailable",
      }),
    );

    expect(message).toMatch(/demo mode replays ai responses recorded in advance/i);
    expect(message).toMatch(/sample job description/i);
    expect(message.toLowerCase()).not.toContain("fixture");
    expect(message.toLowerCase()).not.toContain("hash");
    expect(message.toLowerCase()).not.toContain("sha256");
  });

  it("does not imply the AI read or judged the custom text", () => {
    const message = describeError(Object.assign(new Error("x"), { code: "llm_unavailable" }));

    expect(message).toMatch(/not sent to an ai model/i);
    expect(message).toMatch(/nothing was read from it/i);
    expect(message).not.toMatch(/rejected/i);
  });

  it("tells the user how to proceed", () => {
    const message = describeError(Object.assign(new Error("x"), { code: "llm_unavailable" }));

    expect(message).toMatch(/load the sample/i);
    expect(message).toMatch(/ai provider/i);
  });

  it("explains the confirmation gate in plain words", () => {
    const message = describeError(
      Object.assign(new Error("x"), { code: "requirements_not_confirmed" }),
    );

    expect(message).toMatch(/have not been confirmed/i);
  });

  it("falls back to the original message for anything it does not know", () => {
    expect(describeError(new Error("Could not reach the backend"))).toBe(
      "Could not reach the backend",
    );
    expect(describeError(null)).toBe("Something went wrong.");
  });

  it("recognises the demo limitation as a limitation, not a fault", () => {
    expect(isDemoLimitation(Object.assign(new Error("x"), { code: "llm_unavailable" }))).toBe(true);
    expect(isDemoLimitation(new Error("boom"))).toBe(false);
  });
});
