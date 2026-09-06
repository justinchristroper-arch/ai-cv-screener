import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackendStatus } from "./BackendStatus";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubHealth(body: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body }),
  );
}

describe("BackendStatus", () => {
  it("reports the connected backend's version and environment", async () => {
    stubHealth({ status: "ok", version: "0.1.0", app_env: "development", demo_mode: true });

    render(<BackendStatus />);

    expect(await screen.findByText(/Backend connected/i)).toBeInTheDocument();
    expect(screen.getByText(/v0\.1\.0/)).toBeInTheDocument();
    expect(screen.getByText(/development/)).toBeInTheDocument();
  });

  it("says demo mode serves LLM calls from fixtures", async () => {
    stubHealth({ status: "ok", version: "0.1.0", app_env: "development", demo_mode: true });

    render(<BackendStatus />);

    expect(await screen.findByText(/recorded fixtures/i)).toBeInTheDocument();
  });

  it("distinguishes live mode from demo mode", async () => {
    stubHealth({ status: "ok", version: "0.1.0", app_env: "production", demo_mode: false });

    render(<BackendStatus />);

    expect(await screen.findByText(/configured provider/i)).toBeInTheDocument();
  });

  it("reports an unreachable backend with a recovery hint", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<BackendStatus />);

    expect(await screen.findByText(/Backend unreachable/i)).toBeInTheDocument();
    expect(screen.getByText(/uvicorn app\.main:app/)).toBeInTheDocument();
  });

  it("shows a checking state before the request resolves", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<BackendStatus />);

    expect(screen.getByText(/Checking backend/i)).toBeInTheDocument();
  });
});
