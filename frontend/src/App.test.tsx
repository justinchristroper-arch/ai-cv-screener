import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

beforeEach(() => {
  // The shell must render regardless of backend availability, so the default
  // stub is a failing request.
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App shell", () => {
  it("renders the product name", () => {
    render(<App />);

    expect(screen.getByRole("heading", { level: 1, name: "AI CV Screener" })).toBeInTheDocument();
  });

  it("states that the recruiter makes the decision", () => {
    render(<App />);

    expect(screen.getByText(/the recruiter makes the decision/i)).toBeInTheDocument();
  });

  it("reports the project phase honestly rather than implying a finished product", () => {
    render(<App />);

    expect(screen.getByText(/phase 2 of 20/i)).toBeInTheDocument();
    expect(screen.getByText(/not implemented yet/i)).toBeInTheDocument();
  });

  it("renders both landmark sections", () => {
    render(<App />);

    expect(screen.getByRole("heading", { level: 2, name: /system status/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /project status/i })).toBeInTheDocument();
  });

  it("renders the shell even when the backend is down", async () => {
    render(<App />);

    expect(await screen.findByText(/Backend unreachable/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });
});
