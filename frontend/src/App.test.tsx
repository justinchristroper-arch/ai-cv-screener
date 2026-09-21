import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { DEMO_SAMPLES, HEALTH_DEMO, stubFetch } from "./testing/stubs";

beforeEach(() => {
  window.location.hash = "";
  stubFetch({
    "GET /health": HEALTH_DEMO,
    "GET /api/jobs": { body: [] },
    "GET /api/demo/samples": DEMO_SAMPLES,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("App shell", () => {
  it("renders the product name and what it is for", () => {
    render(<App />);

    expect(screen.getByText("CvScreener")).toBeInTheDocument();
    expect(screen.getByText(/the recruiter decides/i)).toBeInTheDocument();
  });

  it("states in the footer that nothing is ever auto-rejected or hidden", () => {
    render(<App />);

    expect(
      screen.getByText(/never accepts, rejects, filters or hides a candidate/i),
    ).toBeInTheDocument();
  });

  it("does not present scores as predictions of performance", () => {
    render(<App />);

    expect(screen.getByText(/not predictions of how someone will perform/i)).toBeInTheDocument();
  });

  it("routes to the jobs list by default", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: /start a job/i })).toBeInTheDocument();
  });

  it("shows a not-found state for an unrecognised route", () => {
    window.location.hash = "#/nowhere";
    render(<App />);

    expect(screen.getByText(/page not found/i)).toBeInTheDocument();
  });

  it("renders even when the backend is unreachable", async () => {
    vi.unstubAllGlobals();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<App />);

    expect(await screen.findByText(/backend unreachable/i)).toBeInTheDocument();
    expect(screen.getByText("CvScreener")).toBeInTheDocument();
  });
});
