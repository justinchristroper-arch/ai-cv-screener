/**
 * A tiny fetch stub, so a component test can describe the backend it expects.
 *
 * Routes are matched by `METHOD /path`, which keeps each test's setup readable
 * as a list of the endpoints that screen actually calls — and makes an
 * unexpected call fail loudly rather than returning undefined and producing a
 * confusing render.
 */

import { vi } from "vitest";

export interface StubRoute {
  status?: number;
  body?: unknown;
}

export type RouteMap = Record<string, StubRoute | (() => StubRoute)>;

export interface StubOptions {
  /**
   * Resolve responses after a real timer rather than on the next microtask.
   *
   * This matters more than it looks. With instant stubs React can batch a
   * component's "loading" state away before it ever commits, so a bug that only
   * shows while a request is genuinely in flight — a panel blanking itself on
   * refresh, say — silently passes. A few milliseconds of delay makes the
   * intermediate render real, the way it always is against a network.
   */
  delayMs?: number;
}

export interface FetchStub {
  fetch: ReturnType<typeof vi.fn>;
  /** Every request made, as "METHOD /path", in order. */
  calls: string[];
}

export function stubFetch(routes: RouteMap, options: StubOptions = {}): FetchStub {
  const calls: string[] = [];

  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const path = new URL(input, "http://localhost").pathname;
    const key = `${method} ${path}`;
    calls.push(key);

    const entry = routes[key];
    if (entry === undefined) {
      throw new TypeError(`No stub for ${key}`);
    }

    if (options.delayMs) {
      await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    }

    const route = typeof entry === "function" ? entry() : entry;
    const status = route.status ?? 200;
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => route.body ?? {},
    } as Response;
  });

  vi.stubGlobal("fetch", fetchMock);
  return { fetch: fetchMock, calls };
}

export const HEALTH_DEMO = {
  body: {
    status: "ok",
    version: "0.1.0",
    app_env: "development",
    demo_mode: true,
    llm_provider: "ollama",
    llm_model: "claude-opus-5",
  },
};

export const SAMPLE_JD_TEXT =
  "Senior Backend Engineer\nNorthwind Analytics (fictional, sample posting)";

export const SAMPLE_ID_TEXT =
  "saya mau lulusan univ top 10 ptn/pts / harus s1 / bisa bahasa inggris / ipk di atas 3";

export const DEMO_SAMPLES = {
  body: {
    demo_mode: true,
    job_title: "[Demo] Senior Backend Engineer",
    job_description: SAMPLE_JD_TEXT,
    criteria: [
      {
        id: "jd_backend_engineer",
        label: "Formal job description",
        language: "English",
        demonstrates: "A full job posting, the traditional input.",
        text: SAMPLE_JD_TEXT,
        full_walkthrough: true,
      },
      {
        id: "criteria_indonesian",
        label: "Informal criteria, Indonesian",
        language: "Indonesian",
        demonstrates: "Four criteria typed as one line, with local abbreviations.",
        text: SAMPLE_ID_TEXT,
        full_walkthrough: true,
      },
    ],
    cvs: [
      {
        filename: "alex-rivera-backend-engineer.pdf",
        label: "Alex Rivera — strong match",
        demonstrates: "A well-matched CV.",
      },
    ],
  },
};

/** A job whose requirements are confirmed. */
export function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    title: "Senior Backend Engineer",
    requirements_confirmed_at: "2026-09-07T10:00:00Z",
    has_description: true,
    requirement_count: 2,
    created_at: "2026-09-07T09:00:00Z",
    updated_at: "2026-09-07T10:00:00Z",
    ...overrides,
  };
}

export function requirement(overrides: Record<string, unknown> = {}) {
  return {
    id: "req-1",
    job_id: "job-1",
    text: "Strong experience with Python",
    category: "TECHNICAL_SKILL",
    must_have: true,
    weight: "3.00",
    display_order: 0,
    origin: "LLM_EXTRACTED",
    proposed_text: "Strong experience with Python",
    proposed_category: "TECHNICAL_SKILL",
    proposed_must_have: true,
    created_at: "2026-09-07T09:30:00Z",
    updated_at: "2026-09-07T09:30:00Z",
    protected_attribute_flags: [],
    ...overrides,
  };
}

export function evidence(overrides: Record<string, unknown> = {}) {
  return {
    id: "span-1",
    quoted_text: "Python, FastAPI, Postgres, Docker",
    start_char: 10,
    end_char: 42,
    page_number: 1,
    verification_status: "VERIFIED_EXACT",
    normalization_version: "text-normalize-v1",
    ...overrides,
  };
}
