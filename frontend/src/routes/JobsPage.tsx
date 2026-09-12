/**
 * The job list, plus the two ways to start: create one, or seed the demo.
 */

import { useState } from "react";

import {
  createJob,
  getDemoSamples,
  listJobs,
  seedDemoJob,
  type SampleCriteria,
} from "../api/client";
import { EmptyState, ErrorState, Spinner } from "../components/ui";
import { formatDate } from "../display";
import { href, navigate } from "../hooks/useHashRoute";
import { useAction, useResource } from "../hooks/useResource";

export function JobsPage() {
  const { resource, reload } = useResource((signal) => listJobs(signal), []);
  const demo = useResource((signal) => getDemoSamples(signal), []);
  const create = useAction();
  const seed = useAction();
  const [title, setTitle] = useState("");
  // What to seed with. Empty until the person chooses, which is resolved
  // below to the structured demo — the server's default, and the one that
  // needs no model of any kind.
  const [criteriaId, setCriteriaId] = useState("");

  const onCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    let created: { id: string } | null = null;
    const ok = await create.run(async () => {
      created = await createJob(trimmed);
    });
    if (ok && created) {
      setTitle("");
      navigate(href.job((created as { id: string }).id));
    }
  };

  const onSeed = async () => {
    let seeded: { job_id: string } | null = null;
    const ok = await seed.run(async () => {
      seeded = await seedDemoJob(selectedCriteria);
    });
    if (ok && seeded) navigate(href.job((seeded as { job_id: string }).job_id));
  };

  const samples = demo.resource.state === "ready" ? demo.resource.data : null;
  const demoAvailable = samples !== null && samples.demo_mode;
  const criteria: SampleCriteria[] = demoAvailable ? samples.criteria : [];
  // Resolved rather than stored, so the select's value always names an option
  // it actually offers: a controlled select whose value matches no option
  // displays the first entry while reporting something else.
  const structuredId = samples?.structured_criteria_id ?? "structured";
  const selectedCriteria = criteriaId || structuredId;
  const structuredSelected = selectedCriteria === structuredId;

  return (
    <div className="stack">
      <section className="panel">
        <h2>Start a job</h2>
        <form className="form-row" onSubmit={onCreate}>
          <label className="field">
            <span className="field__label">Job title</span>
            <input
              type="text"
              value={title}
              maxLength={200}
              placeholder="Senior Backend Engineer"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <button type="submit" className="button" disabled={create.busy || !title.trim()}>
            {create.busy ? "Creating…" : "Create job"}
          </button>
        </form>
        {create.error ? <ErrorState error={create.error} /> : null}

        {demoAvailable ? (
          <div className="demo-strip">
            <div>
              <p className="demo-strip__title">Or try it with synthetic data</p>
              <p className="demo-strip__hint">
                Seeds a complete job from the bundled sample CVs — a strong match, a CV containing
                injected instructions, and a scan with no text layer. No API key, no cost, and no
                real applicant&rsquo;s data.
              </p>
              <p className="demo-strip__hint">
                {structuredSelected ? (
                  <>
                    The default run uses {samples.structured_criteria.length} structured criteria
                    and calls no language model at all — not even a recorded one. Every verdict is
                    arithmetic over the CV&rsquo;s own text, so it works with nothing installed.
                  </>
                ) : (
                  <>
                    This brief is read by the language model, which in demo mode means a recorded
                    response rather than a live one. It is the earlier approach, kept so the
                    difference is visible.
                  </>
                )}
              </p>
            </div>
            <div className="demo-strip__actions">
              {criteria.length > 0 ? (
                <label className="field field--inline">
                  <span className="field__label">Screen with</span>
                  <select
                    value={selectedCriteria}
                    onChange={(event) => setCriteriaId(event.target.value)}
                  >
                    <option value={samples.structured_criteria_id}>
                      Structured criteria (no model)
                    </option>
                    {criteria.map((item) => (
                      <option key={item.id} value={item.id}>
                        Free text — {item.label} ({item.language})
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <button
                type="button"
                className="button button--secondary"
                onClick={onSeed}
                disabled={seed.busy}
              >
                {seed.busy ? "Seeding…" : "Load demo job"}
              </button>
            </div>
          </div>
        ) : null}
        {seed.error ? <ErrorState error={seed.error} /> : null}
      </section>

      <section className="panel">
        <h2>Jobs</h2>
        {resource.state === "loading" ? <Spinner label="Loading jobs…" /> : null}
        {resource.state === "error" ? (
          <ErrorState
            error={resource.error}
            onRetry={reload}
            hint={
              <>
                Is the backend running? Start it with <code>.\tasks.ps1 dev-backend</code>.
              </>
            }
          />
        ) : null}
        {resource.state === "ready" && resource.data.length === 0 ? (
          <EmptyState title="No jobs yet">
            Create one above, or load the demo job to see the whole workflow with synthetic data.
          </EmptyState>
        ) : null}
        {resource.state === "ready" && resource.data.length > 0 ? (
          <ul className="job-list">
            {resource.data.map((job) => (
              <li key={job.id}>
                <a className="job-card" href={href.job(job.id)}>
                  <span className="job-card__title">{job.title}</span>
                  <span className="job-card__meta">
                    {job.requirement_count} requirement{job.requirement_count === 1 ? "" : "s"}
                    {" · "}
                    {job.requirements_confirmed_at ? "confirmed" : "not confirmed"}
                    {" · "}
                    created {formatDate(job.created_at)}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
