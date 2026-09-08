/**
 * The review step, and the one wall the product deliberately puts in the way.
 *
 * A recruiter can now type criteria in free text, and free text can ask for
 * things a screening system must not screen on. The server refuses to confirm a
 * requirement set containing one, which is the guarantee; these tests cover the
 * other half of it, which is that the person can see the problem while they can
 * still fix it rather than meeting it as a 409.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequirementsPanel } from "./RequirementsPanel";
import { requirement, stubFetch } from "../testing/stubs";

function renderPanel(requirements: ReturnType<typeof requirement>[]) {
  stubFetch({
    "GET /api/jobs/job-1/requirements": {
      body: { job_id: "job-1", requirements_confirmed_at: null, requirements },
    },
  });
  return render(
    <RequirementsPanel jobId="job-1" hasDescription onChanged={() => {}} />,
  );
}

const AGE_FLAG = {
  attribute: "age",
  label: "age or date of birth",
  offset: 0,
  excerpt: "Usia maksimal 25 tahun",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a requirement that names a personal characteristic", () => {
  it("is marked on its own row, in words rather than by colour alone", async () => {
    renderPanel([
      requirement(),
      requirement({
        id: "req-2",
        text: "Usia maksimal 25 tahun",
        display_order: 1,
        protected_attribute_flags: [AGE_FLAG],
      }),
    ]);

    expect(await screen.findByText("Usia maksimal 25 tahun")).toBeInTheDocument();
    expect(screen.getByText("age or date of birth")).toBeInTheDocument();
    expect(screen.getByText(/remove or reword this requirement/i)).toBeInTheDocument();
  });

  it("explains at the confirm button why the set will be refused", async () => {
    renderPanel([
      requirement({
        id: "req-2",
        text: "Usia maksimal 25 tahun",
        protected_attribute_flags: [AGE_FLAG],
      }),
    ]);

    const callout = await screen.findByText(/cannot be confirmed/i);
    const body = callout.closest(".callout") as HTMLElement;

    expect(body).toHaveTextContent(/1 requirement below asks about a personal characteristic/i);
    expect(body).toHaveTextContent(/never extracted from a CV/i);
    // No claim about the candidate, and no automatic rejection of anybody.
    expect(body).not.toHaveTextContent(/reject/i);
  });

  it("leaves an ordinary requirement set alone", async () => {
    renderPanel([requirement(), requirement({ id: "req-2", text: "Experience with Kubernetes" })]);

    expect(await screen.findByText("Strong experience with Python")).toBeInTheDocument();
    expect(screen.queryByText(/cannot be confirmed/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm requirements/i })).toBeEnabled();
  });
});
