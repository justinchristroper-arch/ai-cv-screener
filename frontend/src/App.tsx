/**
 * Application shell and router.
 *
 * The tagline is not decoration. This is decision support: it orders a
 * worklist, and a person makes the decision. That claim is on every screen
 * because it is the one a reader most needs to keep hold of.
 */

import { BackendStatus } from "./components/BackendStatus";
import { EmptyState } from "./components/ui";
import { href, useHashRoute } from "./hooks/useHashRoute";
import { CandidatePage } from "./routes/CandidatePage";
import { JobPage } from "./routes/JobPage";
import { JobsPage } from "./routes/JobsPage";

export function App() {
  const route = useHashRoute();

  return (
    <div className="app">
      <header className="app__header">
        <a className="app__brand" href={href.jobs()}>
          <span className="app__mark" aria-hidden="true">
            ▤
          </span>
          <span>
            <span className="app__name">AI CV Screener</span>
            <span className="app__tagline">
              Evidence-first decision support. The recruiter decides.
            </span>
          </span>
        </a>
        <BackendStatus />
      </header>

      <main className="app__main">
        {route.name === "jobs" ? <JobsPage /> : null}
        {route.name === "job" ? <JobPage jobId={route.jobId} /> : null}
        {route.name === "candidate" ? <CandidatePage candidateId={route.candidateId} /> : null}
        {route.name === "unknown" ? (
          <EmptyState title="Page not found">
            <a href={href.jobs()}>Back to all jobs</a>
          </EmptyState>
        ) : null}
      </main>

      <footer className="app__footer">
        <p>
          This system never accepts, rejects, filters or hides a candidate. Scores and bands are
          summaries of what a document contains, not predictions of how someone will perform.
        </p>
      </footer>
    </div>
  );
}

export default App;
