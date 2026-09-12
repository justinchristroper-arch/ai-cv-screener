/**
 * Step 1 of the workflow, and the screen with the most ways to mislead someone.
 *
 * Three limits meet here, and none of them should be discovered by failing:
 *
 * 1. Demo mode replays AI responses recorded in advance, so it can only analyse
 *    the sample briefs. A person pasting their own text used to find that out
 *    only after pressing Extract, in the words "No recorded fixture for this
 *    input in demo mode." — which reads like the product is broken.
 * 2. Criteria naming a personal characteristic will be refused at confirmation.
 * 3. Text addressed to the system rather than to a reader is flagged, kept and
 *    never obeyed.
 *
 * These tests pin all three, plus the framing that makes the feature usable at
 * all: the box asks for criteria, not for a formal job description.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobDescriptionPanel } from "./JobDescriptionPanel";
import { DEMO_SAMPLES, SAMPLE_ID_TEXT, SAMPLE_JD_TEXT, stubFetch } from "../testing/stubs";

const CUSTOM_TEXT = [
  "saya mau lulusan univ top 10 ptn/pts",
  "harus s1",
  "bisa bahasa inggris",
  "ipk di atas 3",
].join("\n");

function description(
  rawText: string,
  extra: {
    injectionFlagCount?: number;
    protectedFlags?: { attribute: string; label: string }[];
  } = {},
) {
  return {
    body: {
      id: "jd-1",
      job_id: "job-1",
      source_type: "PASTED",
      source_filename: null,
      raw_text: rawText,
      text_sha256: "abc",
      injection_flag_count: extra.injectionFlagCount ?? 0,
      protected_attribute_flags: (extra.protectedFlags ?? []).map((flag) => ({
        ...flag,
        offset: 0,
        excerpt: rawText.slice(0, 40),
      })),
      created_at: "2026-09-07T09:10:00Z",
    },
  };
}

function renderPanel(
  rawText: string,
  samples: typeof DEMO_SAMPLES.body | null,
  extra: Parameters<typeof description>[1] = {},
) {
  stubFetch({ "GET /api/jobs/job-1/description": description(rawText, extra) });
  return render(
    <JobDescriptionPanel jobId="job-1" confirmed={false} samples={samples} onSaved={() => {}} />,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("what the box asks for", () => {
  it("says the box is optional and that the criteria themselves live in step 2", async () => {
    stubFetch({ "GET /api/jobs/job-1/description": { status: 404, body: { message: "none" } } });
    render(
      <JobDescriptionPanel
        jobId="job-1"
        confirmed={false}
        samples={DEMO_SAMPLES.body}
        onSaved={() => {}}
      />,
    );

    expect(await screen.findByText(/in your own words and your own language/i)).toBeInTheDocument();
    // The narrowing in ADR-0012, stated where a recruiter meets it: this text
    // is context, and nothing is screened against it on its own.
    expect(screen.getByText(/you choose what to screen for in step 2/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/job description or notes/i)).toBeInTheDocument();
  });

  it("offers every brief the demo has recordings for, and fills the box from one", async () => {
    const user = userEvent.setup();
    stubFetch({ "GET /api/jobs/job-1/description": { status: 404, body: { message: "none" } } });
    render(
      <JobDescriptionPanel
        jobId="job-1"
        confirmed={false}
        samples={DEMO_SAMPLES.body}
        onSaved={() => {}}
      />,
    );

    expect(await screen.findByText(/Formal job description/)).toBeInTheDocument();
    expect(screen.getByText(/Informal criteria, Indonesian/)).toBeInTheDocument();
    expect(screen.getByText("Indonesian")).toBeInTheDocument();

    const buttons = screen.getAllByRole("button", { name: /use this/i });
    await user.click(buttons[1]);

    expect(screen.getByLabelText(/job description or notes/i)).toHaveValue(SAMPLE_ID_TEXT);
  });
});

describe("demo mode and criteria it has no recording for", () => {
  it("warns about the limitation before the user tries to extract", async () => {
    renderPanel(CUSTOM_TEXT, DEMO_SAMPLES.body);

    expect(await screen.findByText(/cannot be analysed in demo mode/i)).toBeInTheDocument();
    expect(screen.getByText(/replays ai responses recorded in advance/i)).toBeInTheDocument();
  });

  it("does not claim the AI read anything", async () => {
    renderPanel(CUSTOM_TEXT, DEMO_SAMPLES.body);

    const callout = await screen.findByText(/cannot be analysed in demo mode/i);
    const body = callout.closest(".callout") as HTMLElement;

    expect(body).toHaveTextContent(/nothing has been sent to a model/i);
    expect(body).not.toHaveTextContent(/fixture/i);
    expect(body).not.toHaveTextContent(/hash/i);
  });

  it("offers a way out, and says the typed text is not lost", async () => {
    const user = userEvent.setup();
    renderPanel(CUSTOM_TEXT, DEMO_SAMPLES.body);

    const callout = await screen.findByText(/cannot be analysed in demo mode/i);
    const body = callout.closest(".callout") as HTMLElement;
    expect(body).toHaveTextContent(/your text stays until you save/i);

    await user.click(screen.getByRole("button", { name: "Informal criteria, Indonesian" }));
    expect(screen.getByLabelText(/job description or notes/i)).toHaveValue(SAMPLE_ID_TEXT);
  });

  it("says nothing when the saved text is one of the samples", async () => {
    renderPanel(SAMPLE_JD_TEXT, DEMO_SAMPLES.body);

    expect(await screen.findByText(/Northwind Analytics/)).toBeInTheDocument();
    expect(screen.queryByText(/cannot be analysed in demo mode/i)).not.toBeInTheDocument();
  });

  it("says nothing at all when the server is not in demo mode", async () => {
    renderPanel(CUSTOM_TEXT, null);

    expect(await screen.findByText(/lulusan univ top 10/)).toBeInTheDocument();
    expect(screen.queryByText(/demo mode/i)).not.toBeInTheDocument();
  });
});

describe("criteria that name a personal characteristic", () => {
  it("says so, names what it found, and explains why it cannot be screened on", async () => {
    renderPanel("Backend engineer. Wanita, maksimal 25 tahun.", null, {
      protectedFlags: [
        { attribute: "gender", label: "gender" },
        { attribute: "age", label: "age or date of birth" },
      ],
    });

    const callout = await screen.findByText(/asks about a personal characteristic/i);
    const body = callout.closest(".callout") as HTMLElement;

    expect(body).toHaveTextContent(/gender/);
    expect(body).toHaveTextContent(/age or date of birth/);
    expect(body).toHaveTextContent(/could never be answered from evidence/i);
  });

  it("does not alter the text it flagged", async () => {
    const typed = "Backend engineer. Wanita, maksimal 25 tahun.";
    renderPanel(typed, null, { protectedFlags: [{ attribute: "gender", label: "gender" }] });

    expect(await screen.findByText(typed)).toBeInTheDocument();
    expect(screen.getByText(/stored exactly as you typed it/i)).toBeInTheDocument();
  });

  it("says nothing for ordinary criteria", async () => {
    renderPanel("Minimal 2 tahun pengalaman Python", null);

    expect(await screen.findByText(/Minimal 2 tahun/)).toBeInTheDocument();
    expect(screen.queryByText(/personal characteristic/i)).not.toBeInTheDocument();
  });
});

/**
 * Criteria are untrusted text in exactly the way a CV is: a person can paste
 * anything, including a line addressed to the system rather than to a reader.
 * The application never obeys such a line, but the recruiter is the one who
 * confirms the requirements read out of this text, so they are told.
 */
describe("instruction-like text in the criteria", () => {
  it("tells the reviewer when passages read as instructions", async () => {
    renderPanel(SAMPLE_JD_TEXT, null, { injectionFlagCount: 2 });

    const callout = await screen.findByText(/contains instruction-like passages/i);
    const body = callout.closest(".callout") as HTMLElement;

    expect(body).toHaveTextContent(/2 passages/i);
    expect(body).toHaveTextContent(/never removed and never acted on/i);
    expect(body).toHaveTextContent(/you still review and confirm every requirement/i);
  });

  it("says nothing about instructions for ordinary criteria", async () => {
    renderPanel(SAMPLE_JD_TEXT, null);

    expect(await screen.findByText(/Northwind Analytics/)).toBeInTheDocument();
    expect(screen.queryByText(/instruction-like/i)).not.toBeInTheDocument();
  });
});
