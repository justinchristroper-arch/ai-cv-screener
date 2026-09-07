import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobsPage } from "./JobsPage";
import { DEMO_SAMPLES, job, stubFetch } from "../testing/stubs";

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("JobsPage", () => {
  it("shows an empty state that says what to do next", async () => {
    stubFetch({ "GET /api/jobs": { body: [] }, "GET /api/demo/samples": DEMO_SAMPLES });

    render(<JobsPage />);

    expect(await screen.findByText(/no jobs yet/i)).toBeInTheDocument();
    expect(screen.getByText(/load the demo job/i)).toBeInTheDocument();
  });

  it("lists jobs with whether their requirements are confirmed", async () => {
    stubFetch({
      "GET /api/jobs": {
        body: [job(), job({ id: "job-2", title: "Data Analyst", requirements_confirmed_at: null })],
      },
      "GET /api/demo/samples": DEMO_SAMPLES,
    });

    render(<JobsPage />);

    expect(await screen.findByText("Senior Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText(/2 requirements · confirmed/)).toBeInTheDocument();
    expect(screen.getByText(/2 requirements · not confirmed/)).toBeInTheDocument();
  });

  it("offers the demo as clearly synthetic, with no API key needed", async () => {
    stubFetch({ "GET /api/jobs": { body: [] }, "GET /api/demo/samples": DEMO_SAMPLES });

    render(<JobsPage />);

    expect(await screen.findByText(/try it with synthetic data/i)).toBeInTheDocument();
    expect(screen.getByText(/no api key, no cost/i)).toBeInTheDocument();
    expect(screen.getByText(/no real applicant/i)).toBeInTheDocument();
  });

  it("hides the demo offer when the server is not in demo mode", async () => {
    stubFetch({
      "GET /api/jobs": { body: [] },
      "GET /api/demo/samples": { body: { ...DEMO_SAMPLES.body, demo_mode: false } },
    });

    render(<JobsPage />);

    await screen.findByText(/no jobs yet/i);
    expect(screen.queryByRole("button", { name: /load demo job/i })).not.toBeInTheDocument();
  });

  it("creates a job and navigates to it", async () => {
    const user = userEvent.setup();
    stubFetch({
      "GET /api/jobs": { body: [] },
      "GET /api/demo/samples": DEMO_SAMPLES,
      "POST /api/jobs": { status: 201, body: job({ id: "new-job" }) },
    });

    render(<JobsPage />);
    await user.type(screen.getByLabelText(/job title/i), "Platform Engineer");
    await user.click(screen.getByRole("button", { name: /create job/i }));

    expect(window.location.hash).toBe("#/jobs/new-job");
  });

  it("surfaces a backend failure with a way to retry", async () => {
    stubFetch({ "GET /api/demo/samples": DEMO_SAMPLES });

    render(<JobsPage />);

    // A network-level failure is reported as one the user can act on, not as
    // whatever the browser happened to throw.
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the backend/i);
    expect(screen.getByText(/is the backend running\?/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
