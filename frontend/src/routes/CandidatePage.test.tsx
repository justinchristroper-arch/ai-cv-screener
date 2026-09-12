/**
 * The candidate screen carries the claims that matter most, so this is where
 * the wording is asserted rather than left to review: a gap is reported as a
 * gap in the *document*, evidence appears next to the verdict it supports, an
 * unverified quote is visibly refused, and nothing sensitive is on screen.
 */

import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidatePage } from "./CandidatePage";
import { evidence, stubFetch } from "../testing/stubs";

const CANDIDATE = {
  id: "cand-1",
  job_id: "job-1",
  display_name: "Alex Rivera",
  status: "SCORED",
  failure_reason: null,
  failure_detail: null,
  created_at: "2026-09-07T09:00:00Z",
  document: { original_filename: "alex.pdf", size_bytes: 2191, page_count: 1 },
  parsed: { page_count: 1, char_count: 1117, injection_flag_count: 0 },
};

const SCORE = {
  candidate_id: "cand-1",
  job_id: "job-1",
  status: "COMPUTED",
  score: 82,
  score_raw: "0.82258065",
  weighted_sum: "25.5000",
  total_weight: "31.0000",
  must_have_coverage: "0.88888889",
  band: "GOOD_MATCH",
  band_raw: "GOOD_MATCH",
  capped: false,
  capped_by_requirement_id: null,
  capped_by_requirement_text: null,
  review_flag: false,
  needs_review_count: 0,
  must_have_needs_review_count: 0,
  scoring_config_version: "scoring-v1",
  computed_at: "2026-09-07T11:00:00Z",
  contributions: [
    {
      requirement_id: "req-1",
      requirement_text: "Strong experience with Python",
      category: "TECHNICAL_SKILL",
      must_have: true,
      display_order: 0,
      weight: "3.00",
      verdict: "MATCHED",
      verdict_value: "1.0",
      points: "3.000",
    },
    {
      requirement_id: "req-2",
      requirement_text: "Experience with Kubernetes",
      category: "TECHNICAL_SKILL",
      must_have: false,
      display_order: 1,
      weight: "1.00",
      verdict: "NO_EVIDENCE",
      verdict_value: "0",
      points: "0.00",
    },
  ],
};

const MATCHES = {
  candidate_id: "cand-1",
  job_id: "job-1",
  requirements_confirmed_at: "2026-09-07T10:00:00Z",
  results: [
    {
      requirement_id: "req-1",
      requirement_text: "Strong experience with Python",
      category: "TECHNICAL_SKILL",
      must_have: true,
      display_order: 0,
      verdict: "MATCHED",
      decided_by: "DETERMINISTIC_EXACT",
      reason: 'The CV lists "Python" among the candidate\'s skills.',
      evidence: evidence(),
      raw_verdict: null,
      downgraded: false,
    },
    {
      requirement_id: "req-2",
      requirement_text: "Experience with Kubernetes",
      category: "TECHNICAL_SKILL",
      must_have: false,
      display_order: 1,
      verdict: "NO_EVIDENCE",
      decided_by: "LLM_SEMANTIC",
      reason:
        "No evidence found in the CV for this requirement. That is a statement about this document, not about the candidate.",
      evidence: null,
      raw_verdict: null,
      downgraded: false,
    },
  ],
  summary: {
    total: 2,
    matched: 1,
    partial: 0,
    no_evidence: 1,
    needs_review: 0,
    downgraded: 0,
    decided_deterministically: 1,
    decided_by_model: 1,
  },
};

const PROFILE = {
  candidate_id: "cand-1",
  profile_id: "prof-1",
  prompt_version: "profile-extraction-v1",
  created_at: "2026-09-07T10:30:00Z",
  skills: [{ id: "s1", raw_name: "Python", evidence: evidence() }],
  experience: [],
  education: [],
  projects: [],
  evidence_summary: { items: 1, with_evidence: 1, verified: 1, unverified: 0 },
};

function stubCandidate(overrides: Record<string, unknown> = {}) {
  return stubFetch({
    "GET /api/candidates/cand-1": { body: CANDIDATE },
    "GET /api/candidates/cand-1/score": { body: SCORE },
    "GET /api/candidates/cand-1/matches": { body: MATCHES },
    "GET /api/candidates/cand-1/profile": { body: PROFILE },
    ...overrides,
  });
}

beforeEach(() => {
  stubCandidate();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CandidatePage", () => {
  it("shows the score with its band, never the score alone", async () => {
    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText("82")).toBeInTheDocument();
    expect(screen.getAllByText(/good match/i).length).toBeGreaterThan(0);
  });

  it("states that bands are heuristic and not predictions", async () => {
    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText(/heuristic reading aids, not predictions/i)).toBeInTheDocument();
    expect(screen.getByText(/only comparable within this job/i)).toBeInTheDocument();
  });

  it("reports a gap as a fact about the document, never about the person", async () => {
    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByRole("heading", { name: /gaps/i })).toBeInTheDocument();
    expect(screen.getAllByText(/no evidence found in cv/i).length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/statement about (this|the) document, not about the candidate/i).length,
    ).toBeGreaterThan(0);
  });

  it("says the evidence-first framing once per gap, not twice", async () => {
    /* The server already phrases an ordinary NO_EVIDENCE that way, and the UI
       used to add its own copy underneath. Two near-identical sentences in a
       row is how a reader learns to skip both. */
    render(<CandidatePage candidateId="cand-1" />);

    await screen.findByRole("heading", { name: /gaps/i });

    expect(
      screen.getAllByText(/statement about (this|the) document, not about the candidate/i),
    ).toHaveLength(1);
  });

  it("still explains what NO_EVIDENCE means when the reason cannot", async () => {
    /* A downgraded verdict's reason says why the quote was refused, which does
       not carry the framing — so there the note earns its place. */
    stubCandidate({
      "GET /api/candidates/cand-1/matches": {
        body: {
          ...MATCHES,
          results: [
            {
              ...MATCHES.results[1],
              decided_by: "DOWNGRADED_UNVERIFIED",
              downgraded: true,
              raw_verdict: "MATCHED",
              reason: "The proposed evidence could not be found in this CV.",
            },
          ],
        },
      },
    });

    render(<CandidatePage candidateId="cand-1" />);

    expect(
      await screen.findByText(/statement about the document, not about the candidate/i),
    ).toBeInTheDocument();
  });

  it("puts the evidence above the arithmetic", async () => {
    /* A recruiter who reads the number first reads the evidence as a
       justification for it, rather than as the thing it came from. The badge
       stays in the header so an arrival from the ranked list still knows who
       they are looking at. */
    render(<CandidatePage candidateId="cand-1" />);

    const verdicts = await screen.findByRole("heading", { name: /requirement by requirement/i });
    const scorePanel = screen.getByRole("heading", { name: /^score$/i });

    expect(verdicts.compareDocumentPosition(scorePanel)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("never says a candidate lacks or does not have a skill", async () => {
    const { container } = render(<CandidatePage candidateId="cand-1" />);
    await screen.findByText("82");

    const text = container.textContent ?? "";
    expect(text).not.toMatch(/does not have/i);
    expect(text).not.toMatch(/\blacks\b/i);
    expect(text).not.toMatch(/unqualified/i);
    expect(text).not.toMatch(/\breject/i);
  });

  it("puts the quoted evidence next to the verdict it supports", async () => {
    render(<CandidatePage candidateId="cand-1" />);

    // The requirement text also appears in the score breakdown table, so scope
    // the lookup to the verdict list item rather than the whole page.
    await screen.findByText("82");
    const matched = screen
      .getAllByText("Strong experience with Python")
      .map((node) => node.closest("li"))
      .find((node) => node !== null);
    expect(matched).toBeTruthy();
    expect(
      within(matched as HTMLElement).getByText(/Python, FastAPI, Postgres, Docker/),
    ).toBeInTheDocument();
    expect(
      within(matched as HTMLElement).getByText(/verified in the cv, page 1/i),
    ).toBeInTheDocument();
  });

  it("shows a refused quote as refused rather than as evidence", async () => {
    stubCandidate({
      "GET /api/candidates/cand-1/matches": {
        body: {
          ...MATCHES,
          results: [
            {
              ...MATCHES.results[0],
              verdict: "NO_EVIDENCE",
              decided_by: "DOWNGRADED_UNVERIFIED",
              downgraded: true,
              raw_verdict: "MATCHED",
              reason: "The proposed evidence could not be found in this CV.",
              evidence: evidence({
                quoted_text: "Ran Kubernetes clusters for six years.",
                verification_status: "UNVERIFIED",
                start_char: null,
                end_char: null,
                page_number: null,
              }),
            },
            MATCHES.results[1],
          ],
        },
      },
    });

    render(<CandidatePage candidateId="cand-1" />);

    expect(
      await screen.findByText(/not found in the cv — refused as evidence/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/which was refused/i)).toBeInTheDocument();
  });

  it("shows the arithmetic behind the score", async () => {
    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText(/show the arithmetic/i)).toBeInTheDocument();
    expect(screen.getByText(/no model is involved in the arithmetic/i)).toBeInTheDocument();
  });

  it("explains a capped band without implying rejection", async () => {
    stubCandidate({
      "GET /api/candidates/cand-1/score": {
        body: {
          ...SCORE,
          capped: true,
          band: "REVIEW",
          band_raw: "GOOD_MATCH",
          capped_by_requirement_text: "Experience with Kubernetes",
        },
      },
    });

    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText(/band capped at review/i)).toBeInTheDocument();
    expect(screen.getByText(/the candidate is not rejected or hidden/i)).toBeInTheDocument();
  });

  it("states that the profile has no field for a sensitive attribute", async () => {
    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText(/only job-relevant content is extracted/i)).toBeInTheDocument();
    expect(
      screen.getByText(/no field anywhere in this profile for a name, age/i),
    ).toBeInTheDocument();
  });

  it("flags a CV containing instruction-like text", async () => {
    stubCandidate({
      "GET /api/candidates/cand-1": {
        body: { ...CANDIDATE, parsed: { ...CANDIDATE.parsed, injection_flag_count: 2 } },
      },
    });

    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText(/contains instruction-like text/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot be used as evidence/i)).toBeInTheDocument();
  });

  it("treats a missing score as a state rather than an error", async () => {
    stubCandidate({
      "GET /api/candidates/cand-1/score": { status: 404, body: { code: "not_found" } },
      "GET /api/candidates/cand-1/matches": { status: 404, body: { code: "not_found" } },
      "GET /api/candidates/cand-1/profile": { status: 404, body: { code: "not_found" } },
    });

    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText(/not screened yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains an honest processing failure", async () => {
    stubCandidate({
      "GET /api/candidates/cand-1": {
        body: {
          ...CANDIDATE,
          status: "FAILED",
          failure_reason: "NO_TEXT_LAYER",
          failure_detail: "The PDF contains no extractable text.",
        },
      },
      "GET /api/candidates/cand-1/score": { status: 404, body: {} },
      "GET /api/candidates/cand-1/matches": { status: 404, body: {} },
      "GET /api/candidates/cand-1/profile": { status: 404, body: {} },
    });

    render(<CandidatePage candidateId="cand-1" />);

    expect(await screen.findByText(/could not be processed/i)).toBeInTheDocument();
    expect(screen.getByText(/does not perform OCR/i)).toBeInTheDocument();
  });

  it("renders CV-derived markup as inert text", async () => {
    stubCandidate({
      "GET /api/candidates/cand-1/matches": {
        body: {
          ...MATCHES,
          results: [
            {
              ...MATCHES.results[0],
              evidence: evidence({ quoted_text: "<img src=x onerror=alert(1)>Python" }),
            },
          ],
          summary: { ...MATCHES.summary, total: 1, no_evidence: 0 },
        },
      },
    });

    const { container } = render(<CandidatePage candidateId="cand-1" />);
    await screen.findByText("82");

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>Python/)).toBeInTheDocument();
  });
});
