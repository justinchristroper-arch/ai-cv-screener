/**
 * The one component that makes a claim about where a candidate's CV goes.
 *
 * There are now three states with genuinely different privacy properties —
 * nothing runs, a model runs on the server's own machine, a model runs at a
 * third party — and telling a user the wrong one is the worst mistake this
 * component could make. So the copy is driven by what the server reports, and
 * these tests pin each state to its own wording.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackendStatus } from "./BackendStatus";
import { HEALTH_DEMO, stubFetch } from "../testing/stubs";

function stubHealth(overrides: Record<string, unknown> = {}) {
  stubFetch({ "GET /health": { body: { ...HEALTH_DEMO.body, ...overrides } } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("demo mode", () => {
  it("says responses are replayed, and why that limits what works", async () => {
    stubHealth();

    render(<BackendStatus />);

    expect(await screen.findByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText(/no model runs, nothing is sent anywhere/i)).toBeInTheDocument();
    expect(
      screen.getByText(/only the sample briefs and sample CVs can be analysed/i),
    ).toBeInTheDocument();
  });

  it("uses no internal terminology a recruiter would not recognise", async () => {
    stubHealth();

    const { container } = render(<BackendStatus />);
    await screen.findByText("Demo mode");

    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toContain("fixture");
    expect(text).not.toContain("sha256");
    expect(text).not.toContain("hash");
  });

  it("says nothing about a provider, because none is contacted", async () => {
    /* Demo mode is the outer switch. Naming a provider here would imply one is
       involved, which is the specific confusion this state exists to avoid. */
    stubHealth({ llm_provider: "ollama", llm_model: "qwen2.5:7b-instruct" });

    const { container } = render(<BackendStatus />);
    await screen.findByText("Demo mode");

    expect(container.textContent).not.toMatch(/ollama/i);
    expect(container.textContent).not.toMatch(/qwen/i);
  });
});

describe("local AI mode", () => {
  it("names the model and says the work happens on the server's machine", async () => {
    stubHealth({
      demo_mode: false,
      llm_provider: "ollama",
      llm_model: "qwen2.5:7b-instruct",
    });

    render(<BackendStatus />);

    expect(await screen.findByText("Local AI mode")).toBeInTheDocument();
    expect(screen.getByText(/qwen2\.5:7b-instruct/)).toBeInTheDocument();
    expect(screen.getByText(/not sent to a third-party AI service/i)).toBeInTheDocument();
    expect(screen.getByText(/Ollama must be running/i)).toBeInTheDocument();
  });

  it("does not claim the data never leaves the user's own computer", async () => {
    /* It leaves the browser and reaches the server. Whether that server is the
       same laptop is a deployment question this component cannot answer, so it
       says "the server this app is talking to" rather than overpromising. */
    stubHealth({ demo_mode: false, llm_provider: "ollama", llm_model: "qwen2.5:7b-instruct" });

    const { container } = render(<BackendStatus />);
    await screen.findByText("Local AI mode");

    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toContain("never leaves your computer");
    expect(text).not.toContain("completely private");
    expect(text).not.toContain("fully private");
    expect(text).toContain("the server this app is talking to");
  });

  it("does not warn about cost, because there is none", async () => {
    stubHealth({ demo_mode: false, llm_provider: "ollama", llm_model: "qwen2.5:7b-instruct" });

    const { container } = render(<BackendStatus />);
    await screen.findByText("Local AI mode");

    expect(container.textContent).not.toMatch(/costs money/i);
  });
});

describe("cloud AI mode", () => {
  it("says the documents go to a third party and that it costs money", async () => {
    stubHealth({
      demo_mode: false,
      app_env: "production",
      llm_provider: "anthropic",
      llm_model: "claude-opus-5",
    });

    render(<BackendStatus />);

    expect(await screen.findByText("Cloud AI mode")).toBeInTheDocument();
    expect(screen.getByText(/sent to a third-party AI provider/i)).toBeInTheDocument();
    expect(screen.getByText(/each run costs money/i)).toBeInTheDocument();
  });

  it("is not described as local, and does not claim the data stays put", async () => {
    /* The API base URL is often http://localhost:8000, so a bare search for
       "local" would match the wrong thing. What must not appear is the *claim*. */
    stubHealth({ demo_mode: false, llm_provider: "anthropic", llm_model: "claude-opus-5" });

    const { container } = render(<BackendStatus />);
    await screen.findByText("Cloud AI mode");

    const text = (container.textContent ?? "").toLowerCase();
    expect(screen.queryByText("Local AI mode")).not.toBeInTheDocument();
    expect(text).not.toContain("running on the server this app is talking to");
    expect(text).not.toContain("not sent to a third-party");
  });

  it("treats DeepSeek as the third party it is, and names its model", async () => {
    /* Anything that is not Ollama gets the cloud copy. This pins it for the
       hosted provider a deployment actually uses. */
    stubHealth({ demo_mode: false, llm_provider: "deepseek", llm_model: "deepseek-flash" });

    const { container } = render(<BackendStatus />);

    expect(await screen.findByText("Cloud AI mode")).toBeInTheDocument();
    expect(
      screen.getByText(/sent to a third-party AI provider \(deepseek-flash\)/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/each run costs money/i)).toBeInTheDocument();
    expect((container.textContent ?? "").toLowerCase()).not.toContain("not sent to a third-party");
  });
});

describe("an unreachable backend", () => {
  it("reports it with something actionable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<BackendStatus />);

    expect(await screen.findByText(/backend unreachable/i)).toBeInTheDocument();
    expect(screen.getByText(/dev-backend/)).toBeInTheDocument();
  });
});
