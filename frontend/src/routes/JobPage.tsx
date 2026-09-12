/**
 * One job, end to end: description, requirements, candidates, results.
 *
 * The four steps are numbered on screen because the order is not a convention —
 * the confirmation gate genuinely blocks step 4 until step 2 is finished, and a
 * recruiter should be able to see why before they hit it.
 */

import { getDemoSamples, getJob } from "../api/client";
import { CandidatesPanel } from "../components/CandidatesPanel";
import { CriteriaPanel } from "../components/CriteriaPanel";
import { JobDescriptionPanel } from "../components/JobDescriptionPanel";
import { ErrorState, Pill, Spinner } from "../components/ui";
import { href } from "../hooks/useHashRoute";
import { useResource } from "../hooks/useResource";

export function JobPage({ jobId }: { jobId: string }) {
  const { resource, reload } = useResource((signal) => getJob(jobId, signal), [jobId]);
  const demo = useResource((signal) => getDemoSamples(signal), []);

  if (resource.state === "loading") return <Spinner label="Loading job…" />;
  if (resource.state === "error") {
    return (
      <ErrorState
        error={resource.error}
        onRetry={reload}
        hint={<a href={href.jobs()}>Back to all jobs</a>}
      />
    );
  }

  const job = resource.data;
  const confirmed = job.requirements_confirmed_at !== null;
  const samples =
    demo.resource.state === "ready" && demo.resource.data.demo_mode ? demo.resource.data : null;
  const isDemoJob = job.title.startsWith("[Demo]");

  return (
    <div className="stack">
      <nav className="crumbs">
        <a href={href.jobs()}>All jobs</a>
        <span aria-hidden="true">›</span>
        <span>{job.title}</span>
      </nav>

      <header className="page-header">
        <h1>{job.title}</h1>
        <div className="page-header__tags">
          {isDemoJob ? <Pill tone="demo">Synthetic demo data</Pill> : null}
          <Pill tone={confirmed ? "ok" : "warn"}>
            {confirmed ? "Criteria confirmed" : "Criteria not confirmed"}
          </Pill>
        </div>
      </header>

      <JobDescriptionPanel jobId={jobId} confirmed={confirmed} samples={samples} onSaved={reload} />
      <CriteriaPanel jobId={jobId} hasDescription={job.has_description} onChanged={reload} />
      <CandidatesPanel jobId={jobId} confirmed={confirmed} />
    </div>
  );
}
