import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackendStatus } from "./BackendStatus";
import { stubFetch } from "../testing/stubs";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BackendStatus", () => {
  it("says demo mode replays recorded fixtures, and why that limits what works", async () => {
    stubFetch({
      "GET /health": {
        body: { status: "ok", version: "0.1.0", app_env: "development", demo_mode: true },
      },
    });

    render(<BackendStatus />);

    expect(await screen.findByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText(/no api key, no cost/i)).toBeInTheDocument();
    expect(screen.getByText(/only the bundled synthetic documents replay/i)).toBeInTheDocument();
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
