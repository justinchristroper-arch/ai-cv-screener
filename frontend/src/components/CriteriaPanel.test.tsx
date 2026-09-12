/**
 * Choosing what to screen for, now that the choice is a closed one.
 *
 * These tests are mostly about the boundary rather than about the form
 * mechanics. ADR-0012 narrowed the product to a closed set of criteria it can
 * answer from a CV by arithmetic, and the whole value of that narrowing depends
 * on the interface being honest about it: the menu has to say those are all
 * there is, an unsupported skill has to be refused in words a recruiter can act
 * on,
 * and the free-text path has to remain reachable with its cost stated rather
 * than quietly removed.
 *
 * The confirmation gate (ADR-0004) moved into this panel with the rest of step
 * 2, so it is covered here too.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CriteriaPanel } from "./CriteriaPanel";
import { VOCABULARY, criterion, requirement, stubFetch } from "../testing/stubs";

type Row = ReturnType<typeof requirement>;

function renderPanel(
  rows: Row[] = [],
  options: { confirmed?: boolean; extra?: Record<string, unknown> } = {},
) {
  const confirmedAt = options.confirmed ? "2026-09-07T10:00:00Z" : null;
  const stub = stubFetch({
    "GET /api/criteria/vocabulary": VOCABULARY,
    "GET /api/jobs/job-1/requirements": {
      body: { job_id: "job-1", requirements_confirmed_at: confirmedAt, requirements: rows },
    },
    ...options.extra,
  });
  render(<CriteriaPanel jobId="job-1" hasDescription onChanged={() => {}} />);
  return stub;
}

/** The JSON body of the nth POST to the criteria endpoint. */
function postedCriteria(stub: ReturnType<typeof stubFetch>): unknown[] {
  return stub.fetch.mock.calls
    .filter(
      (call) =>
        String(call[0]).endsWith("/criteria") &&
        (call[1] as RequestInit | undefined)?.method === "POST",
    )
    .map((call) => JSON.parse(String((call[1] as RequestInit).body)));
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /add criterion/i }));
}

async function chooseType(user: ReturnType<typeof userEvent.setup>, label: RegExp) {
  await openMenu(user);
  await user.click(screen.getByRole("button", { name: label }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --------------------------------------------------------------------------
// The boundary, stated in the interface
// --------------------------------------------------------------------------

describe("the criteria a recruiter is offered", () => {
  it("offers exactly the types the engine can evaluate, and says so", async () => {
    const user = userEvent.setup();
    renderPanel();
    await openMenu(user);

    // Anchored to the start of the accessible name: each menu item's name is
    // its label followed by its hint, and the hints mention words like
    // "skill", so an unanchored match finds more than one button.
    for (const label of [
      "Minimum degree",
      "Minimum GPA",
      "Work experience duration",
      "Skill",
      "Internship",
      "Language",
      "Work experience in a field",
      "Certification",
    ]) {
      expect(screen.getByRole("button", { name: new RegExp(`^${label}`) })).toBeInTheDocument();
    }

    expect(screen.getByText(/other criteria aren.t supported yet/i)).toBeInTheDocument();
  });

  it("is usable with no job description at all", async () => {
    // Criteria used to be extracted from a description, so the panel used to be
    // blocked without one. They are chosen directly now.
    const user = userEvent.setup();
    const stub = stubFetch({
      "GET /api/criteria/vocabulary": VOCABULARY,
      "GET /api/jobs/job-1/requirements": {
        body: { job_id: "job-1", requirements_confirmed_at: null, requirements: [] },
      },
      "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() },
    });
    render(<CriteriaPanel jobId="job-1" hasDescription={false} onChanged={() => {}} />);

    await chooseType(user, /^Internship/);
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    await waitFor(() => expect(postedCriteria(stub)).toHaveLength(1));
  });

  it("says what each criterion honestly does before it is chosen", async () => {
    const user = userEvent.setup();
    renderPanel();
    await openMenu(user);

    // The two limits most likely to be assumed away.
    expect(
      screen.getByText(/never infers how well someone speaks a language/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/you state the scale/i)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// The skill search — where the limit is actually met
// --------------------------------------------------------------------------

describe("searching for a skill", () => {
  it("refuses an unsupported one in words, rather than accepting it silently", async () => {
    const user = userEvent.setup();
    renderPanel();
    await chooseType(user, /^Skill/);

    await user.type(screen.getByLabelText(/^skill$/i), "Blockchain");

    expect(await screen.findByText(/skill not currently supported/i)).toBeInTheDocument();
    // The reason matters as much as the refusal: this is our gap, not theirs.
    expect(
      screen.getByText(/reporting a gap in our vocabulary as a gap in the candidate/i),
    ).toBeInTheDocument();
    // And there is no way to add it anyway.
    expect(screen.getByRole("button", { name: /^add criterion$/i })).toBeDisabled();
  });

  it("offers the supported ones and posts the canonical name", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });
    await chooseType(user, /^Skill/);

    await user.type(screen.getByLabelText(/^skill$/i), "postgre");
    await user.click(await screen.findByRole("button", { name: "PostgreSQL" }));
    // Scoped: the free-text form behind the disclosure has a "Must have" box too.
    const form = screen.getByRole("form", { name: /new criterion/i });
    await user.click(within(form).getByLabelText(/must have/i));
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([
        { spec_type: "SKILL", must_have: true, subject: "PostgreSQL" },
      ]),
    );
  });

  it("adds a preset as several ordinary criteria, not as one vague one", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });

    await user.click(await screen.findByRole("button", { name: /cloud & infrastructure \(2\)/i }));

    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([
        { spec_type: "SKILL", must_have: false, subject: "Docker" },
        { spec_type: "SKILL", must_have: false, subject: "Kubernetes" },
      ]),
    );
  });

  it("surfaces the server's refusal when a subject is outside the list", async () => {
    const user = userEvent.setup();
    renderPanel([], {
      extra: {
        "POST /api/jobs/job-1/criteria": {
          status: 409,
          body: { code: "conflict", message: "'Docker' is not a skill this screener supports." },
        },
      },
    });

    await chooseType(user, /^Skill/);
    await user.type(screen.getByLabelText(/^skill$/i), "Docker");
    await user.click(await screen.findByRole("button", { name: "Docker" }));
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /not a skill this screener supports/i,
    );
  });
});

// --------------------------------------------------------------------------
// The typed fields
// --------------------------------------------------------------------------

describe("the fields each criterion collects", () => {
  it("asks for the GPA scale rather than assuming one", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });
    await chooseType(user, /^Minimum GPA/);

    const scale = screen.getByLabelText(/out of/i);
    // Only the scales a CV states in a form this screener reads. A third would
    // let a recruiter set a minimum no CV could be compared against.
    expect(within(scale as HTMLSelectElement).getAllByRole("option")).toHaveLength(2);
    expect(
      screen.getByText(/on a different scale is left for review, not converted/i),
    ).toBeInTheDocument();

    // The grade is required: the form will not submit without it.
    expect(screen.getByRole("button", { name: /^add criterion$/i })).toBeDisabled();

    await user.type(screen.getByLabelText(/minimum grade/i), "3.25");
    await user.selectOptions(scale, "5.00");
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([
        {
          spec_type: "GPA_MIN",
          must_have: false,
          threshold_value: "3.25",
          threshold_scale: "5.00",
        },
      ]),
    );
  });

  it("reads a duration back in the words a person would use", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });
    await chooseType(user, /^Work experience duration/);

    await user.type(screen.getByLabelText(/minimum duration in months/i), "24");
    expect(screen.getByText("= 2 years")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));
    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([
        { spec_type: "EXPERIENCE_MIN", must_have: false, threshold_value: "24" },
      ]),
    );
  });

  it("lets an internship criterion ask for presence alone", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });
    await chooseType(user, /^Internship/);

    expect(screen.getByLabelText(/minimum duration in months \(optional\)/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([{ spec_type: "INTERNSHIP_MIN", must_have: false }]),
    );
  });

  it("offers a language as presence only, chosen from the supported list", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });
    await chooseType(user, /^Language/);

    const select = screen.getByLabelText(/^language$/i);
    await user.selectOptions(select, "Indonesian");
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([
        { spec_type: "LANGUAGE_PRESENT", must_have: false, subject: "Indonesian" },
      ]),
    );
  });
});

// --------------------------------------------------------------------------
// The list, and what decided each row
// --------------------------------------------------------------------------

describe("the criteria already on the job", () => {
  it("names what will decide each one", async () => {
    renderPanel([
      criterion(),
      requirement({ id: "req-9", text: "Comfortable mentoring juniors", display_order: 1 }),
    ]);

    expect(await screen.findByText("Skill: Python")).toBeInTheDocument();
    // The distinction that matters: reproducible arithmetic over the CV's own
    // words, or a model's reading of a sentence.
    expect(screen.getByText("Screening rules")).toBeInTheDocument();
    expect(screen.getByText("Language model")).toBeInTheDocument();
    expect(screen.getByText(/1 judged by the model/i)).toBeInTheDocument();
  });

  it("keeps weight and must-have editable, as before", async () => {
    renderPanel([criterion()]);

    expect(await screen.findByLabelText(/weight for: skill: python/i)).toHaveValue(3);
    expect(screen.getByLabelText(/must have: skill: python/i)).toBeInTheDocument();
  });

  it("flags a criterion that names a personal characteristic", async () => {
    renderPanel([
      requirement({
        id: "req-2",
        text: "Usia maksimal 25 tahun",
        protected_attribute_flags: [
          { attribute: "age", label: "age or date of birth", offset: 0, excerpt: "Usia" },
        ],
      }),
    ]);

    expect(await screen.findByText("age or date of birth")).toBeInTheDocument();
    expect(screen.getByText(/cannot be confirmed/i)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// The free-text path, kept but demoted
// --------------------------------------------------------------------------

describe("the free-text path", () => {
  it("is still reachable, and states what it costs", async () => {
    renderPanel([criterion()]);

    expect(
      await screen.findByText(/screen for something these criteria cannot express/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/read by the language model rather than by the screening rules/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /re-extract from the description/i }),
    ).toBeInTheDocument();
  });

  it("is not offered once the set is confirmed", async () => {
    renderPanel([criterion()], { confirmed: true });

    expect(await screen.findByText(/criteria confirmed/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/screen for something these criteria cannot express/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add criterion/i })).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// The gate
// --------------------------------------------------------------------------

describe("the confirmation gate", () => {
  it("still blocks screening until a human confirms", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([criterion()], {
      extra: {
        "POST /api/jobs/job-1/requirements/confirm": {
          body: {
            job_id: "job-1",
            requirements_confirmed_at: "2026-09-07T12:00:00Z",
            requirements: [],
          },
        },
      },
    });

    expect(
      await screen.findByText(/confirmation is required before screening/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /confirm criteria/i }));

    await waitFor(() => expect(stub.calls).toContain("POST /api/jobs/job-1/requirements/confirm"));
  });

  it("says what stays editable afterwards, and what unconfirming costs", async () => {
    renderPanel([criterion()], { confirmed: true });

    expect(await screen.findByText(/weight and must-have stay editable/i)).toBeInTheDocument();
    expect(screen.getByText(/discards every verdict and score in this job/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /unconfirm and edit/i })).toBeInTheDocument();
  });

  it("offers nothing to confirm when there is nothing on the job yet", async () => {
    renderPanel([]);

    expect(await screen.findByText(/no criteria yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm criteria/i })).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// The one criterion that collects a subject and a number together
// --------------------------------------------------------------------------

describe("experience in a field", () => {
  it("asks for both the skill and the duration, and posts both", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });
    await chooseType(user, /^Work experience in a field/);

    const form = screen.getByRole("form", { name: /new criterion/i });
    // Neither half alone is enough: the submit stays disabled until both are in.
    expect(screen.getByRole("button", { name: /^add criterion$/i })).toBeDisabled();

    await user.type(within(form).getByLabelText(/^skill$/i), "postgre");
    await user.click(await screen.findByRole("button", { name: "PostgreSQL" }));
    expect(screen.getByRole("button", { name: /^add criterion$/i })).toBeDisabled();

    await user.type(within(form).getByLabelText(/minimum duration in months/i), "24");
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([
        {
          spec_type: "EXPERIENCE_IN_FIELD",
          must_have: false,
          subject: "PostgreSQL",
          threshold_value: "24",
        },
      ]),
    );
  });

  it("says that unrelated experience does not answer it", async () => {
    const user = userEvent.setup();
    renderPanel();
    await openMenu(user);

    expect(screen.getByText(/unrelated experience does not answer it/i)).toBeInTheDocument();
  });
});

describe("certification", () => {
  it("offers the supported credentials and posts the chosen one", async () => {
    const user = userEvent.setup();
    const stub = renderPanel([], {
      extra: { "POST /api/jobs/job-1/criteria": { status: 201, body: criterion() } },
    });
    await chooseType(user, /^Certification/);

    const select = screen.getByLabelText(/^certification/i);
    await user.selectOptions(select, "Brevet A");
    await user.click(screen.getByRole("button", { name: /^add criterion$/i }));

    await waitFor(() =>
      expect(postedCriteria(stub)).toEqual([
        { spec_type: "CERTIFICATION_PRESENT", must_have: false, subject: "Brevet A" },
      ]),
    );
  });

  it("states the limits of a credential check where it is chosen", async () => {
    const user = userEvent.setup();
    renderPanel();
    await chooseType(user, /^Certification/);

    expect(
      screen.getByText(/date is never compared, and one credential never stands in for another/i),
    ).toBeInTheDocument();
  });
});
