/**
 * Upload and screening, including what happens when a candidate cannot be
 * screened. The orchestration lives in the browser — profile, then matching,
 * then scoring, per candidate — so this is where that sequence is pinned down.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidatesPanel } from "./CandidatesPanel";
import { stubFetch } from "../testing/stubs";

function ranking(overrides: Record<string, unknown> = {}) {
  return {
    job_id: "job-1",
    job_title: "Senior Backend Engineer",
    requirements_confirmed_at: "2026-09-07T10:00:00Z",
    ranked: [],
    not_yet_scored: [],
    failed: [],
    summary: { total: 0, ranked: 0, not_yet_scored: 0, failed: 0 },
    ...overrides,
  };
}

const PENDING = {
  candidate_id: "cand-1",
  display_name: null,
  original_filename: "alex.pdf",
  status: "PARSED",
};

function pdf(name = "alex.pdf") {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])], name, {
    type: "application/pdf",
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CandidatesPanel", () => {
  it("says screening is blocked until requirements are confirmed", async () => {
    stubFetch({ "GET /api/jobs/job-1/ranking": { body: ranking() } });

    render(<CandidatesPanel jobId="job-1" confirmed={false} />);

    expect(await screen.findByText(/screening is blocked/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /screen \d+ candidate/i })).not.toBeInTheDocument();
  });

  it("reports each uploaded file's outcome, accepted or not", async () => {
    const user = userEvent.setup();
    stubFetch({
      "GET /api/jobs/job-1/ranking": { body: ranking() },
      "POST /api/jobs/job-1/candidates": {
        status: 201,
        body: {
          job_id: "job-1",
          uploaded: 1,
          rejected: 1,
          results: [
            {
              filename: "alex.pdf",
              accepted: true,
              candidate_id: "cand-1",
              status: "PARSED",
              failure_reason: null,
              rejection_code: null,
              detail: null,
            },
            {
              filename: "notes.txt.pdf",
              accepted: false,
              candidate_id: null,
              status: null,
              failure_reason: null,
              rejection_code: "not_a_pdf",
              detail: "The file is named .pdf but its contents are not a PDF.",
            },
          ],
        },
      },
    });

    const { container } = render(<CandidatesPanel jobId="job-1" confirmed />);
    await screen.findByText(/no candidates yet/i);

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, [pdf(), pdf("notes.txt.pdf")]);

    expect(await screen.findByText("alex.pdf")).toBeInTheDocument();
    expect(screen.getByText("Accepted")).toBeInTheDocument();
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText(/its contents are not a PDF/i)).toBeInTheDocument();
  });

  it("runs profile, matching and scoring in order for each candidate", async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      "GET /api/jobs/job-1/ranking": {
        body: ranking({
          not_yet_scored: [PENDING],
          summary: { total: 1, ranked: 0, not_yet_scored: 1, failed: 0 },
        }),
      },
      "POST /api/candidates/cand-1/profile": { status: 201, body: {} },
      "POST /api/candidates/cand-1/matches": { status: 201, body: {} },
      "POST /api/candidates/cand-1/score": { status: 201, body: {} },
    });

    render(<CandidatesPanel jobId="job-1" confirmed />);
    await user.click(await screen.findByRole("button", { name: /screen 1 candidate/i }));

    await waitFor(() => expect(screen.getByText(/1 of 1 processed/i)).toBeInTheDocument());
    expect(calls).toContain("POST /api/candidates/cand-1/profile");
    expect(calls.indexOf("POST /api/candidates/cand-1/profile")).toBeLessThan(
      calls.indexOf("POST /api/candidates/cand-1/matches"),
    );
    expect(calls.indexOf("POST /api/candidates/cand-1/matches")).toBeLessThan(
      calls.indexOf("POST /api/candidates/cand-1/score"),
    );
  });

  it("reports a candidate that could not be screened without stopping the rest", async () => {
    const user = userEvent.setup();
    stubFetch({
      "GET /api/jobs/job-1/ranking": {
        body: ranking({
          not_yet_scored: [
            PENDING,
            { ...PENDING, candidate_id: "cand-2", original_filename: "b.pdf" },
          ],
          summary: { total: 2, ranked: 0, not_yet_scored: 2, failed: 0 },
        }),
      },
      "POST /api/candidates/cand-1/profile": {
        status: 503,
        body: {
          code: "llm_unavailable",
          message: "No recorded fixture for this input in demo mode.",
        },
      },
      "POST /api/candidates/cand-2/profile": { status: 201, body: {} },
      "POST /api/candidates/cand-2/matches": { status: 201, body: {} },
      "POST /api/candidates/cand-2/score": { status: 201, body: {} },
    });

    render(<CandidatesPanel jobId="job-1" confirmed />);
    await user.click(await screen.findByRole("button", { name: /screen 2 candidates/i }));

    await waitFor(() => expect(screen.getByText(/2 of 2 processed/i)).toBeInTheDocument());
    expect(screen.getByText(/1 could not be screened/i)).toBeInTheDocument();
    expect(screen.getByText(/no recorded fixture for this input/i)).toBeInTheDocument();
  });

  it("shows the ranked list with the band beside every score", async () => {
    stubFetch({
      "GET /api/jobs/job-1/ranking": {
        body: ranking({
          ranked: [
            {
              position: 1,
              candidate_id: "cand-1",
              display_name: "Alex Rivera",
              original_filename: "alex.pdf",
              status: "SCORED",
              score_status: "COMPUTED",
              score: 82,
              must_have_coverage: "0.88888889",
              band: "GOOD_MATCH",
              band_raw: "GOOD_MATCH",
              capped: false,
              matched_count: 8,
              warnings: ["INSTRUCTION_LIKE_TEXT_IN_CV"],
              scoring_config_version: "scoring-v1",
              computed_at: "2026-09-07T11:00:00Z",
            },
          ],
          summary: { total: 1, ranked: 1, not_yet_scored: 0, failed: 0 },
        }),
      },
    });

    render(<CandidatesPanel jobId="job-1" confirmed />);

    expect(await screen.findByText("Alex Rivera")).toBeInTheDocument();
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getByText("Good match")).toBeInTheDocument();
    expect(screen.getByText(/must-have coverage 89%/i)).toBeInTheDocument();
    expect(screen.getByText(/reads as an instruction/i)).toBeInTheDocument();
    expect(screen.getByText(/not a hiring decision/i)).toBeInTheDocument();
  });
});
