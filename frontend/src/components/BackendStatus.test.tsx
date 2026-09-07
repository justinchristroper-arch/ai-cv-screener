import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackendStatus } from "./BackendStatus";
import { stubFetch } from "../testing/stubs";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BackendStatus", () => {
  it("says demo mode replays recordings, and why that limits what works", async () => {
    stubFetch({
      "GET /health": {
        body: { status: "ok", version: "0.1.0", app_env: "development", demo_mode: true },
      },
    });

    render(<BackendStatus />);

    expect(await screen.findByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText(/no api key, no cost/i)).toBeInTheDocument();
    expect(screen.getByText(/only the sample documents can be analysed/i)).toBeInTheDocument();
  });

  it("uses no internal terminology a recruiter would not recognise", async () => {
    stubFetch({
      "GET /health": {
        body: { status: "ok", version: "0.1.0", app_env: "development", demo_mode: true },
      },
    });

    const { container } = render(<BackendStatus />);
    await screen.findByText("Demo mode");

    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toContain("fixture");
    expect(text).not.toContain("sha256");
    expect(text).not.toContain("hash");
  });

  it("says live mode uses the configured provider", async () => {
    stubFetch({
      "GET /health": {
        body: { status: "ok", version: "0.1.0", app_env: "production", demo_mode: false },
      },
    });

    render(<BackendStatus />);

    expect(await screen.findByText("Live mode")).toBeInTheDocument();
    expect(screen.getByText(/configured provider/i)).toBeInTheDocument();
  });

  it("reports an unreachable backend with something actionable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<BackendStatus />);

    expect(await screen.findByText(/backend unreachable/i)).toBeInTheDocument();
    expect(screen.getByText(/dev-backend/)).toBeInTheDocument();
  });
});
