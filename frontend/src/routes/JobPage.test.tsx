/**
 * The job screen is where the confirmation gate is made visible, so that is
 * what these tests are mostly about: an unconfirmed job must say why screening
 * is blocked, and a confirmed one must say what unconfirming would cost.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobPage } from "./JobPage";
import { DEMO_SAMPLES, job, requirement, stubFetch } from "../testing/stubs";

const EMPTY_RANKING = {
  body: {
    job_id: "job-1",
    job_title: "Senior Backend Engineer",
    requirements_confirmed_at: null,
    ranked: [],
    not_yet_scored: [],
    failed: [],
    summary: { total: 0, ranked: 0, not_yet_scored: 0, failed: 0 },
  },
};

function stubJob(options: { confirmed: boolean; ranking?: unknown } = { confirmed: true }) {
  const confirmedAt = options.confirmed ? "2026-09-07T10:00:00Z" : null;
  return stubFetch({
    "GET /health": {
      body: { status: "ok", version: "0.1.0", app_env: "development", demo_mode: true },
    },
    "GET /api/demo/samples": DEMO_SAMPLES,
    "GET /api/jobs/job-1": { body: job({ requirements_confirmed_at: confirmedAt }) },
    "GET /api/jobs/job-1/description": {
      body: {
        id: "jd-1",
        job_id: "job-1",
        source_type: "PASTED",
        source_filename: null,
        raw_text: "Senior Backend Engineer at Northwind Analytics",
        text_sha256: "abc",
        created_at: "2026-09-07T09:10:00Z",
      },
    },
    "GET /api/jobs/job-1/requirements": {
      body: {
        job_id: "job-1",
        requirements_confirmed_at: confirmedAt,
        requirements: [
          requirement(),
          requirement({
            id: "req-2",
            text: "Experience with Kubernetes",
            must_have: false,
            weight: "1.00",
          }),
        ],
      },
    },
    "GET /api/jobs/job-1/ranking": (options.ranking as { body: unknown }) ?? EMPTY_RANKING,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("JobPage", () => {
  it("numbers the workflow so the order is visible", async () => {
    stubJob({ confirmed: true });

    render(<JobPage jobId="job-1" />);

    expect(
      await screen.findByRole("heading", { name: /1 · job description/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /2 · requirements/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /3 · candidates/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /4 · results/i })).toBeInTheDocument();
  });

  it("explains that screening is blocked until requirements are confirmed", async () => {
    stubJob({ confirmed: false });

    render(<JobPage jobId="job-1" />);

    expect(
      await screen.findByText(/confirmation is required before screening/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /nothing is matched or scored against a requirement set no one has agreed to/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm requirements/i })).toBeInTheDocument();
  });

  it("says what stays editable after confirmation, and what unconfirming costs", async () => {
    stubJob({ confirmed: true });

    render(<JobPage jobId="job-1" />);

    // "Requirements confirmed" appears twice — the header pill and the callout
    // title — and they arrive from two separate requests, so query for the
    // callout body instead of the ambiguous phrase.
    expect(await screen.findByText(/weight and must-have stay editable/i)).toBeInTheDocument();
    expect(screen.getByText(/discards every verdict and score in this job/i)).toBeInTheDocument();
    expect(screen.getAllByText(/requirements confirmed/i).length).toBeGreaterThan(0);
  });

  it("labels a demo job as synthetic", async () => {
    stubFetch({
      "GET /health": {
        body: { status: "ok", version: "0.1.0", app_env: "development", demo_mode: true },
      },
      "GET /api/demo/samples": DEMO_SAMPLES,
      "GET /api/jobs/job-1": { body: job({ title: "[Demo] Senior Backend Engineer" }) },
      "GET /api/jobs/job-1/description": { status: 404, body: { code: "not_found" } },
      "GET /api/jobs/job-1/requirements": {
        body: { job_id: "job-1", requirements_confirmed_at: null, requirements: [] },
      },
      "GET /api/jobs/job-1/ranking": EMPTY_RANKING,
    });

    render(<JobPage jobId="job-1" />);

    expect(await screen.findByText(/synthetic demo data/i)).toBeInTheDocument();
  });

  it("treats a job with no description yet as a starting state, not an error", async () => {
    stubFetch({
      "GET /health": {
        body: { status: "ok", version: "0.1.0", app_env: "development", demo_mode: true },
      },
      "GET /api/demo/samples": DEMO_SAMPLES,
      "GET /api/jobs/job-1": { body: job({ has_description: false, requirement_count: 0 }) },
      "GET /api/jobs/job-1/description": { status: 404, body: { code: "not_found" } },
      "GET /api/jobs/job-1/requirements": {
        body: { job_id: "job-1", requirements_confirmed_at: null, requirements: [] },
      },
      "GET /api/jobs/job-1/ranking": EMPTY_RANKING,
    });

    render(<JobPage jobId="job-1" />);

    expect(await screen.findByLabelText(/paste the job description/i)).toBeInTheDocument();
    expect(screen.getByText(/add a job description first/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers the synthetic sample description in demo mode", async () => {
    stubJob({ confirmed: false });

    render(<JobPage jobId="job-1" />);
    // The description already exists, so the editor is behind "Replace".
    (await screen.findByRole("button", { name: /replace/i })).click();

    expect(
      await screen.findByRole("button", { name: /use the sample job description/i }),
    ).toBeInTheDocument();
  });

  it("warns before replacing a description, not after", async () => {
    stubJob({ confirmed: true });

    render(<JobPage jobId="job-1" />);
    (await screen.findByRole("button", { name: /replace/i })).click();

    expect(await screen.findByText(/replacing this text is not free/i)).toBeInTheDocument();
    expect(screen.getByText(/are deleted and the job is unconfirmed/i)).toBeInTheDocument();
  });

  it("accounts for every candidate, including those that failed", async () => {
    stubJob({
      confirmed: true,
      ranking: {
        body: {
          job_id: "job-1",
          job_title: "Senior Backend Engineer",
          requirements_confirmed_at: "2026-09-07T10:00:00Z",
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
              warnings: [],
              scoring_config_version: "scoring-v1",
              computed_at: "2026-09-07T11:00:00Z",
            },
          ],
          not_yet_scored: [
            {
              candidate_id: "cand-2",
              display_name: null,
              original_filename: "pending.pdf",
              status: "PARSED",
            },
          ],
          failed: [
            {
              candidate_id: "cand-3",
              display_name: null,
              original_filename: "scan.pdf",
              status: "FAILED",
              failure_reason: "NO_TEXT_LAYER",
              failure_detail: "No extractable text.",
            },
          ],
          summary: { total: 3, ranked: 1, not_yet_scored: 1, failed: 1 },
        },
      },
    });

    render(<JobPage jobId="job-1" />);

    expect(await screen.findByText("Alex Rivera")).toBeInTheDocument();
    expect(
      screen.getByText(/3 candidates · 1 ranked · 1 not yet screened · 1 failed/),
    ).toBeInTheDocument();
    expect(screen.getByText("pending.pdf")).toBeInTheDocument();
    expect(screen.getByText("scan.pdf")).toBeInTheDocument();
    expect(
      screen.getByText(/a file you uploaded should never disappear silently/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/does not perform OCR/i)).toBeInTheDocument();
  });
});
