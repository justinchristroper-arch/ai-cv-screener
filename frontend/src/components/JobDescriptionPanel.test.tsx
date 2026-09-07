/**
 * Demo mode has a real limitation: it replays AI responses recorded in advance,
 * so it can only extract requirements from the sample job description. A person
 * pasting their own text used to discover that only after pressing Extract, in
 * the words "No recorded fixture for this input in demo mode." — which reads
 * like the product is broken and means nothing to a recruiter.
 *
 * These tests pin the two halves of the fix: the limitation is stated before the
 * action, and the failure — if it still happens — is explained in language a
 * person can act on, without claiming the AI read anything.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobDescriptionPanel } from "./JobDescriptionPanel";
import { DEMO_SAMPLES, stubFetch } from "../testing/stubs";

const SAMPLE_TEXT = DEMO_SAMPLES.body.job_description;

const CUSTOM_TEXT = [
  "saya mau lulusan univ top 10 ptn/pts",
  "harus s1",
  "bisa bahasa inggris",
  "ipk di atas 3",
].join("\n");

function description(rawText: string) {
  return {
    body: {
      id: "jd-1",
      job_id: "job-1",
      source_type: "PASTED",
      source_filename: null,
      raw_text: rawText,
      text_sha256: "abc",
      created_at: "2026-09-07T09:10:00Z",
    },
  };
}

function renderPanel(rawText: string, samples: typeof DEMO_SAMPLES.body | null) {
  stubFetch({ "GET /api/jobs/job-1/description": description(rawText) });
  return render(
    <JobDescriptionPanel jobId="job-1" confirmed={false} samples={samples} onSaved={() => {}} />,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("demo mode and a custom job description", () => {
  it("warns about the limitation before the user tries to extract", async () => {
    renderPanel(CUSTOM_TEXT, DEMO_SAMPLES.body);

    expect(await screen.findByText(/cannot be analysed in demo mode/i)).toBeInTheDocument();
    expect(screen.getByText(/replays ai responses recorded in advance/i)).toBeInTheDocument();
  });

  it("does not suggest the AI read the custom text", async () => {
    const { container } = renderPanel(CUSTOM_TEXT, DEMO_SAMPLES.body);
    await screen.findByText(/cannot be analysed in demo mode/i);

    expect(screen.getByText(/nothing has been sent to a model/i)).toBeInTheDocument();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/rejected/i);
    expect(text).not.toMatch(/could not understand/i);
  });

  it("says what to do instead, and that the text is not lost", async () => {
    renderPanel(CUSTOM_TEXT, DEMO_SAMPLES.body);
    await screen.findByText(/cannot be analysed in demo mode/i);

    expect(
      screen.getByRole("button", { name: /replace it with the sample job description/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/an ai provider configured/i)).toBeInTheDocument();
    expect(screen.getByText(/your text stays until you save/i)).toBeInTheDocument();
  });

  it("never shows internal fixture or hash wording", async () => {
    const { container } = renderPanel(CUSTOM_TEXT, DEMO_SAMPLES.body);
    await screen.findByText(/cannot be analysed in demo mode/i);

    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toContain("fixture");
    expect(text).not.toContain("sha256");
    expect(text).not.toContain("hash");
    expect(text).not.toContain("prompt_version");
  });

  it("stays quiet when the description is the supported sample", async () => {
    renderPanel(SAMPLE_TEXT, DEMO_SAMPLES.body);

    expect(await screen.findByText(/Northwind Analytics/)).toBeInTheDocument();
    expect(screen.queryByText(/cannot be analysed in demo mode/i)).not.toBeInTheDocument();
  });

  it("stays quiet when the server is not in demo mode", async () => {
    // `samples` is null whenever demo mode is off, so a custom description is
    // simply a custom description.
    renderPanel(CUSTOM_TEXT, null);

    expect(await screen.findByText(/lulusan univ top 10/)).toBeInTheDocument();
    expect(screen.queryByText(/cannot be analysed in demo mode/i)).not.toBeInTheDocument();
  });
});
