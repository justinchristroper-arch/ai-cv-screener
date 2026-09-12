/**
 * What the candidate screen does with a criterion it could not determine.
 *
 * ADR-0012 added a fourth verdict so that "the CV shows nothing" and "the CV
 * shows something we could not read" would stop being the same answer. That is
 * only worth anything if the screen keeps them apart, so these tests assert the
 * wording rather than the markup: an unresolved criterion must never be
 * presented as a gap, must never appear in the arithmetic as a zero, and must
 * be visible next to the number it is missing from.
 */

import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidatePage } from "./CandidatePage";
import { stubFetch } from "../testing/stubs";

const CANDIDATE = {
  id: "cand-1",
  job_id: "job-1",
  display_name: "Rana Idris",
  status: "SCORED",
  failure_reason: null,
  failure_detail: null,
  created_at: "2026-09-07T09:00:00Z",
  document: { original_filename: "rana.pdf", size_bytes: 1800, page_count: 1 },
  parsed: { page_count: 1, char_count: 900, injection_flag_count: 0 },
};

const UNRESOLVED_GPA = {
  requirement_id: "req-2",
  requirement_text: "Minimum GPA: 3.00 / 4.00",
  category: "EDUCATION",
  must_have: true,
  display_order: 1,
  verdict: "NEEDS_REVIEW",
  decided_by: "DETERMINISTIC_STRUCTURED",
  reason:
    "The CV states a grade but not the scale it is out of, so it cannot be compared with the minimum asked for.",
  evidence: null,
  raw_verdict: null,
  downgraded: false,
};

const MATCHED_SKILL = {
  requirement_id: "req-1",
  requirement_text: "Skill: Python",
  category: "TECHNICAL_SKILL",
  must_have: false,
  display_order: 0,
  verdict: "MATCHED",
  decided_by: "DETERMINISTIC_STRUCTURED",
  reason: "The CV evidences Python.",
  evidence: {
    id: "span-1",
    quoted_text: "Python, Docker, SQL",
    start_char: 100,
    end_char: 119,
    page_number: 1,
    verification_status: "VERIFIED_EXACT",
    normalization_version: "text-normalize-v1",
  },
  raw_verdict: null,
  downgraded: false,
};

function matches(results: unknown[], needsReview: number) {
  return {
    candidate_id: "cand-1",
    job_id: "job-1",
    requirements_confirmed_at: "2026-09-07T10:00:00Z",
    results,
    summary: {
      total: results.length,
      matched: results.length - needsReview,
      partial: 0,
      no_evidence: 0,
      needs_review: needsReview,
      downgraded: 0,
      decided_deterministically: results.length,
      decided_by_model: 0,
    },
  };
}

/** A score computed over the criteria that did resolve, with one that did not. */
const PARTLY_RESOLVED = {
  candidate_id: "cand-1",
  job_id: "job-1",
  status: "COMPUTED",
  score: 100,
  score_raw: "1.00000000",
  weighted_sum: "1.0000",
  total_weight: "1.0000",
  must_have_coverage: null,
  band: "REVIEW",
  band_raw: "STRONG_MATCH",
  capped: true,
  capped_by_requirement_id: "req-2",
  capped_by_requirement_text: "Minimum GPA: 3.00 / 4.00",
  review_flag: true,
  needs_review_count: 1,
  must_have_needs_review_count: 1,
  scoring_config_version: "scoring-v1",
  computed_at: "2026-09-07T11:00:00Z",
  contributions: [
    {
      requirement_id: "req-1",
      requirement_text: "Skill: Python",
      category: "TECHNICAL_SKILL",
      must_have: false,
      display_order: 0,
      weight: "1.00",
      verdict: "MATCHED",
      verdict_value: "1.0",
      points: "1.000",
    },
    {
      requirement_id: "req-2",
      requirement_text: "Minimum GPA: 3.00 / 4.00",
      category: "EDUCATION",
      must_have: true,
      display_order: 1,
      weight: "3.00",
      verdict: "NEEDS_REVIEW",
      verdict_value: null,
      points: null,
    },
  ],
};

const NOTHING_DECIDABLE = {
  ...PARTLY_RESOLVED,
  status: "UNDEFINED_NO_DECIDABLE",
  score: null,
  score_raw: null,
  weighted_sum: null,
  total_weight: null,
  band: null,
  band_raw: null,
  capped: false,
  capped_by_requirement_id: null,
  capped_by_requirement_text: null,
  contributions: [PARTLY_RESOLVED.contributions[1]],
};

function renderPage(score: unknown, results: unknown[], needsReview: number) {
  stubFetch({
    "GET /api/candidates/cand-1": { body: CANDIDATE },
    "GET /api/candidates/cand-1/score": { body: score },
    "GET /api/candidates/cand-1/matches": { body: matches(results, needsReview) },
    "GET /api/candidates/cand-1/profile": { status: 404, body: { code: "not_found" } },
  });
  return render(<CandidatePage candidateId="cand-1" />);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a criterion that could not be determined", () => {
  it("is listed apart from the gaps, and says it is unresolved rather than unmet", async () => {
    renderPage(PARTLY_RESOLVED, [MATCHED_SKILL, UNRESOLVED_GPA], 1);

    const heading = await screen.findByRole("heading", { name: /could not be determined/i });
    expect(heading).toBeInTheDocument();
    expect(screen.getByText(/unresolved rather than unmet/i)).toBeInTheDocument();
    // The gaps group is for genuine absence, and there is none here.
    expect(screen.queryByRole("heading", { name: /^gaps/i })).not.toBeInTheDocument();
  });

  it("says what stopped it, in terms of the document", async () => {
    renderPage(PARTLY_RESOLVED, [MATCHED_SKILL, UNRESOLVED_GPA], 1);

    expect(
      await screen.findByText(/states a grade but not the scale it is out of/i),
    ).toBeInTheDocument();
    // Never a claim about the person, and never a claim that evidence is absent.
    expect(screen.queryByText(/does not have/i)).not.toBeInTheDocument();
  });

  it("appears in the arithmetic as excluded, not as a zero", async () => {
    renderPage(PARTLY_RESOLVED, [MATCHED_SKILL, UNRESOLVED_GPA], 1);

    const row = (await screen.findByText("Minimum GPA: 3.00 / 4.00", { selector: "td" })).closest(
      "tr",
    ) as HTMLElement;

    expect(within(row).getByText("excluded")).toBeInTheDocument();
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    // Its weight is still printed, so the exclusion is visible rather than implied.
    expect(within(row).getByText("3")).toBeInTheDocument();
  });

  it("warns that the number covers less of the job than the list suggests", async () => {
    renderPage(PARTLY_RESOLVED, [MATCHED_SKILL, UNRESOLVED_GPA], 1);

    const callout = await screen.findByText(/does not cover every criterion/i);
    const body = callout.closest(".callout") as HTMLElement;

    expect(body).toHaveTextContent(/1 of 2 criteria could not be determined/i);
    expect(body).toHaveTextContent(/rather than counted as zeros/i);
    expect(body).toHaveTextContent(/weight was not shared out among the others/i);
  });
});

describe("a band capped by something unresolved", () => {
  it("says the criterion was undetermined, not that it was missing", async () => {
    renderPage(PARTLY_RESOLVED, [MATCHED_SKILL, UNRESOLVED_GPA], 1);

    const callout = await screen.findByText(/band capped at review/i);
    const body = callout.closest(".callout") as HTMLElement;

    expect(body).toHaveTextContent(/could not be determined from this CV/i);
    expect(body).toHaveTextContent(/not the same as it being unmet/i);
    // The wording for genuine absence must not appear here.
    expect(body).not.toHaveTextContent(/has no evidence in this CV/i);
    expect(body).toHaveTextContent(/not rejected or hidden/i);
  });
});

describe("when nothing at all could be determined", () => {
  it("reports no score rather than a zero", async () => {
    renderPage(NOTHING_DECIDABLE, [UNRESOLVED_GPA], 1);

    expect(await screen.findByText(/no score could be formed/i)).toBeInTheDocument();
    expect(screen.getByText(/could not determine any of them/i)).toBeInTheDocument();
    expect(screen.getByText(/not a score of zero/i)).toBeInTheDocument();
    // The badge shows an em dash, never a 0.
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("still says the CV is worth reading", async () => {
    renderPage(NOTHING_DECIDABLE, [UNRESOLVED_GPA], 1);

    expect(
      await screen.findByText(/the CV itself is unchanged and still worth reading/i),
    ).toBeInTheDocument();
  });
});
