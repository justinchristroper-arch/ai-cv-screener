/**
 * Inline requirement edits must not disturb the page around them.
 *
 * The bug these guard against: every edit called back up to the job page, whose
 * resource dropped to a loading state and replaced the whole page with a
 * spinner. The document collapsed to one line, the browser clamped the scroll
 * position to the top, and all three panels remounted and refetched. To the
 * person editing, it looked like the page had reloaded itself.
 *
 * These stubs answer after a **real timer**, which is the detail that makes the
 * tests meaningful. With instant stubs React batches the intermediate "loading"
 * render away before it ever commits, and the bug passes unnoticed — as it did
 * when this suite was first written. A few milliseconds of delay makes the
 * in-flight render real, the way it always is against a network.
 *
 * A refetch of data that did not change is the observable symptom of a remount,
 * so that is what is asserted.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobPage } from "./JobPage";
import { HEALTH_DEMO, DEMO_SAMPLES, job, requirement, stubFetch } from "../testing/stubs";

const DELAY = { delayMs: 5 };

const HEALTH = {
  body: HEALTH_DEMO.body,
};

const RANKING = {
  body: {
    job_id: "job-1",
    job_title: "Senior Backend Engineer",
    requirements_confirmed_at: "2026-09-07T10:00:00Z",
    ranked: [],
    not_yet_scored: [],
    failed: [],
    summary: { total: 0, ranked: 0, not_yet_scored: 0, failed: 0 },
  },
};

const DESCRIPTION = {
  body: {
    id: "jd-1",
    job_id: "job-1",
    source_type: "PASTED",
    source_filename: null,
    raw_text: "Senior Backend Engineer at Northwind Analytics",
    text_sha256: "abc",
    injection_flag_count: 0,
    protected_attribute_flags: [],
    created_at: "2026-09-07T09:10:00Z",
  },
};

/** Server-side state the stubs mutate, so an edit is genuinely reflected back. */
let mustHave = true;
let weight = "3.00";

function stubJob() {
  mustHave = true;
  weight = "3.00";
  return stubFetch(
    {
      "GET /health": HEALTH,
      "GET /api/demo/samples": DEMO_SAMPLES,
      "GET /api/jobs/job-1": { body: job() },
      "GET /api/jobs/job-1/description": DESCRIPTION,
      "GET /api/jobs/job-1/requirements": () => ({
        body: {
          job_id: "job-1",
          requirements_confirmed_at: "2026-09-07T10:00:00Z",
          requirements: [
            requirement({ must_have: mustHave, weight }),
            requirement({
              id: "req-2",
              text: "Experience with Kubernetes",
              must_have: false,
              weight: "1.00",
            }),
          ],
        },
      }),
      "GET /api/jobs/job-1/ranking": RANKING,
      "PATCH /api/requirements/req-1": () => {
        mustHave = !mustHave;
        return { body: requirement({ must_have: mustHave, weight }) };
      },
    },
    DELAY,
  );
}

function countOf(calls: string[], key: string): number {
  return calls.filter((call) => call === key).length;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("inline requirement edits", () => {
  it("persists a must-have change without remounting the rest of the page", async () => {
    const user = userEvent.setup();
    const { calls } = stubJob();

    render(<JobPage jobId="job-1" />);
    const checkbox = await screen.findByLabelText(/must have: strong experience with python/i);
    expect(checkbox).toBeChecked();

    const descriptionsBefore = countOf(calls, "GET /api/jobs/job-1/description");
    const rankingsBefore = countOf(calls, "GET /api/jobs/job-1/ranking");

    await user.click(checkbox);

    await waitFor(() => expect(calls).toContain("PATCH /api/requirements/req-1"));
    await waitFor(() =>
      expect(screen.getByLabelText(/must have: strong experience with python/i)).not.toBeChecked(),
    );

    // Only a remount would re-request these, and neither changed.
    expect(countOf(calls, "GET /api/jobs/job-1/description")).toBe(descriptionsBefore);
    expect(countOf(calls, "GET /api/jobs/job-1/ranking")).toBe(rankingsBefore);
    // Nor is the job itself worth refetching: the header shows neither weight
    // nor the must-have flag.
    expect(countOf(calls, "GET /api/jobs/job-1")).toBe(1);
  });

  it("never blanks the page while an edit is in flight", async () => {
    const user = userEvent.setup();
    stubJob();

    render(<JobPage jobId="job-1" />);
    const checkbox = await screen.findByLabelText(/must have: strong experience with python/i);

    const click = user.click(checkbox);
    // Sampled *during* the request, which is when the old code showed a
    // full-page spinner and the scroll position collapsed.
    await new Promise((resolve) => setTimeout(resolve, 2));
    expect(screen.queryByText(/loading job…/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/loading requirements…/i)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /1 · screening criteria/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /4 · results/i })).toBeInTheDocument();
    expect(screen.getByText("Strong experience with Python")).toBeInTheDocument();

    await click;
  });

  it("keeps the other rows usable while one row is being saved", async () => {
    const user = userEvent.setup();
    stubJob();

    render(<JobPage jobId="job-1" />);
    const first = await screen.findByLabelText(/must have: strong experience with python/i);
    const second = screen.getByLabelText(/must have: experience with kubernetes/i);

    const click = user.click(first);
    await new Promise((resolve) => setTimeout(resolve, 2));

    // The old code disabled every input on the panel while any single edit was
    // in flight, which blurred whatever control the person was using.
    expect(second).not.toBeDisabled();

    await click;
  });

  it("persists a weight change and shows the stored value", async () => {
    const user = userEvent.setup();
    weight = "3.00";
    const { calls } = stubFetch(
      {
        "GET /health": HEALTH,
        "GET /api/demo/samples": DEMO_SAMPLES,
        "GET /api/jobs/job-1": { body: job() },
        "GET /api/jobs/job-1/description": DESCRIPTION,
        "GET /api/jobs/job-1/requirements": () => ({
          body: {
            job_id: "job-1",
            requirements_confirmed_at: "2026-09-07T10:00:00Z",
            requirements: [requirement({ weight })],
          },
        }),
        "GET /api/jobs/job-1/ranking": RANKING,
        "PATCH /api/requirements/req-1": () => {
          weight = "7.00";
          return { body: requirement({ weight }) };
        },
      },
      DELAY,
    );

    render(<JobPage jobId="job-1" />);
    const input = await screen.findByLabelText(/weight for: strong experience with python/i);
    expect(input).toHaveValue(3);

    await user.clear(input);
    await user.type(input, "7");
    await user.tab();

    await waitFor(() => expect(calls).toContain("PATCH /api/requirements/req-1"));
    await waitFor(() =>
      expect(screen.getByLabelText(/weight for: strong experience with python/i)).toHaveValue(7),
    );
    expect(countOf(calls, "GET /api/jobs/job-1/ranking")).toBe(1);
  });

  it("still refreshes the job header when the requirement set itself changes", async () => {
    // Confirming is not like editing a weight: it changes job-level state the
    // header displays, so refetching the job there is correct.
    const user = userEvent.setup();
    let confirmedAt: string | null = null;
    const { calls } = stubFetch(
      {
        "GET /health": HEALTH,
        "GET /api/demo/samples": DEMO_SAMPLES,
        "GET /api/jobs/job-1": () => ({ body: job({ requirements_confirmed_at: confirmedAt }) }),
        "GET /api/jobs/job-1/description": { status: 404, body: { code: "not_found" } },
        "GET /api/jobs/job-1/requirements": () => ({
          body: {
            job_id: "job-1",
            requirements_confirmed_at: confirmedAt,
            requirements: [requirement()],
          },
        }),
        "GET /api/jobs/job-1/ranking": RANKING,
        "POST /api/jobs/job-1/requirements/confirm": () => {
          confirmedAt = "2026-09-07T12:00:00Z";
          return {
            body: { job_id: "job-1", requirements_confirmed_at: confirmedAt, requirements: [] },
          };
        },
      },
      DELAY,
    );

    render(<JobPage jobId="job-1" />);
    await user.click(await screen.findByRole("button", { name: /confirm requirements/i }));

    await waitFor(() => expect(countOf(calls, "GET /api/jobs/job-1")).toBeGreaterThan(1));
    const header = screen.getByRole("banner");
    await waitFor(() =>
      expect(within(header).queryByText(/requirements not confirmed/i)).not.toBeInTheDocument(),
    );
  });
  it("shows the weight the server stored, not the one that was typed", async () => {
    // The backend stores NUMERIC(5,2), so "7.005" comes back as "7.01". The box
    // is uncontrolled — it must still re-sync to the saved value.
    const user = userEvent.setup();
    let stored = "3.00";
    stubFetch(
      {
        "GET /health": HEALTH,
        "GET /api/demo/samples": DEMO_SAMPLES,
        "GET /api/jobs/job-1": { body: job() },
        "GET /api/jobs/job-1/description": DESCRIPTION,
        "GET /api/jobs/job-1/requirements": () => ({
          body: {
            job_id: "job-1",
            requirements_confirmed_at: "2026-09-07T10:00:00Z",
            requirements: [requirement({ weight: stored })],
          },
        }),
        "GET /api/jobs/job-1/ranking": RANKING,
        "PATCH /api/requirements/req-1": () => {
          stored = "7.01";
          return { body: requirement({ weight: stored }) };
        },
      },
      DELAY,
    );

    render(<JobPage jobId="job-1" />);
    const input = await screen.findByLabelText(/weight for: strong experience with python/i);
    await user.clear(input);
    await user.type(input, "7.005");
    await user.tab();

    await waitFor(() =>
      expect(screen.getByLabelText(/weight for: strong experience with python/i)).toHaveValue(7.01),
    );
  });
});
