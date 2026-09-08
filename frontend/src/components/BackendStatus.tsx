/**
 * A compact backend indicator, in the header.
 *
 * It says which mode the server is in, because that changes what the product
 * can do: in demo mode every model call is replayed from a recorded fixture, so
 * the workflow only completes for the bundled synthetic documents. A user who
 * uploads their own CV in demo mode and gets a "no recorded fixture" error
 * deserves to have been told why beforehand.
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
      <p className="backend backend--checking" role="status">
        Checking backend…
      </p>
    );
  }

  if (status.state === "unreachable") {
    return (
      <div className="backend backend--error" role="status">
        <strong>Backend unreachable</strong>
        <span className="backend__hint">
          {status.message}. Start it with <code>.\tasks.ps1 dev-backend</code>.
        </span>
      </div>
    );
  }

  const { health } = status;
  return (
    <div className={`backend ${health.demo_mode ? "backend--ok" : "backend--live"}`} role="status">
      <strong>{health.demo_mode ? "Demo mode" : "Live AI mode"}</strong>
      <span className="backend__hint">
        {health.demo_mode
          ? "AI responses are replayed from recordings made in advance — no API key, no cost, and nothing is sent anywhere. Only the sample briefs and sample CVs can be analysed."
          : `Your criteria and every CV you upload are sent to the configured AI provider, and each run costs money. v${health.version} · ${API_BASE_URL}`}
      </span>
    </div>
  );
}
