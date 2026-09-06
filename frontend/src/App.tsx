/**
 * Application shell.
 *
 * Layout and honest status only. The screening workflow — job creation,
 * requirement review, CV upload, ranking — is Phase 11.
 */

import { BackendStatus } from "./components/BackendStatus";

export function App() {
  return (
    <div className="app">
      <header className="app__header">
        <h1>AI CV Screener</h1>
        <p className="app__tagline">
          Decision support for CV screening. The recruiter makes the decision.
        </p>
      </header>

      <main className="app__main">
        <section aria-labelledby="status-heading">
          <h2 id="status-heading">System status</h2>
          <BackendStatus />
        </section>

        <section aria-labelledby="phase-heading">
          <h2 id="phase-heading">Project status</h2>
          <p>
            Phase 2 of 20 — local development environment. The screening pipeline is not implemented
            yet.
          </p>
          <p className="app__hint">
            See <code>docs/roadmap.md</code> for what each phase delivers.
          </p>
        </section>
      </main>
    </div>
  );
}

export default App;
