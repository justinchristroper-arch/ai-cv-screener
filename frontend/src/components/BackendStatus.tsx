/**
 * Shows whether the backend is reachable.
 *
 * This is the whole of the Phase 2 frontend/backend integration: enough to
 * prove the two halves talk to each other (and that CORS is configured), and
 * no more. The screening UI arrives in Phase 11.
 */

import { useEffect, useState } from "react";

import { API_BASE_URL, fetchHealth, type HealthResponse } from "../api/client";

type Status =
  | { state: "checking" }
  | { state: "connected"; health: HealthResponse }
  | { state: "unreachable"; message: string };

export function BackendStatus() {
  const [status, setStatus] = useState<Status>({ state: "checking" });

  useEffect(() => {
    const controller = new AbortController();

    fetchHealth(controller.signal)
      .then((health) => setStatus({ state: "connected", health }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setStatus({
          state: "unreachable",
          message: error instanceof Error ? error.message : "Unknown error",
        });
      });

    return () => controller.abort();
  }, []);

  if (status.state === "checking") {
    return (
      <p className="status status--checking" role="status">
        Checking backend…
      </p>
    );
  }

  if (status.state === "unreachable") {
    return (
      <div className="status status--error" role="status">
        <p>
          <strong>Backend unreachable.</strong> {status.message}
        </p>
        <p className="status__hint">
          Start it with <code>uvicorn app.main:app --reload</code> from <code>backend/</code>.
        </p>
      </div>
    );
  }

  const { health } = status;
  return (
    <div className="status status--ok" role="status">
      <p>
        <strong>Backend connected.</strong> v{health.version} · {health.app_env}
      </p>
      <p className="status__hint">
        {health.demo_mode
          ? "Demo mode: LLM calls will be served from recorded fixtures."
          : "Live mode: LLM calls will use the configured provider."}
      </p>
      <p className="status__hint">
        API base: <code>{API_BASE_URL}</code>
      </p>
    </div>
  );
}
