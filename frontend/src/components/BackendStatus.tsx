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

  if (health.demo_mode) {
    return (
      <div className="backend backend--ok" role="status">
        <strong>Demo mode</strong>
        <span className="backend__hint">
          AI responses are replayed from recordings made in advance — no model runs, nothing is sent
          anywhere, and no API key is needed. Only the sample briefs and sample CVs can be analysed.
        </span>
      </div>
    );
  }

  // Two live modes with genuinely different privacy properties, so they get
  // genuinely different copy. Claiming "your data stays on your machine" while
  // running against a cloud API would be the worst mistake this component
  // could make, so the wording is driven by what the server reports rather
  // than by a build-time assumption.
  const local = health.llm_provider === "ollama";
  return (
    <div className={`backend ${local ? "backend--local" : "backend--live"}`} role="status">
      <strong>{local ? "Local AI mode" : "Cloud AI mode"}</strong>
      <span className="backend__hint">
        {local
          ? `Your criteria and every CV you upload are read by ${health.llm_model} running on the server this app is talking to — not sent to a third-party AI service. Ollama must be running with that model installed.`
          : `Your criteria and every CV you upload are sent to a third-party AI provider (${health.llm_model}), and each run costs money.`}{" "}
        v{health.version} · {API_BASE_URL}
      </span>
    </div>
  );
}
