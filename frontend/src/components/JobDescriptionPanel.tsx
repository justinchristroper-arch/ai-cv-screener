/**
 * Step 1: the job description.
 *
 * Replacing the text has consequences the recruiter should not discover
 * afterwards — extracted requirements are deleted and the job is unconfirmed —
 * so they are stated before the button, not after it.
 */

import { useEffect, useState } from "react";

import { ApiError, getJobDescription, setJobDescription, type DemoSamples } from "../api/client";
import { useAction, useResource } from "../hooks/useResource";
import { Callout, ErrorState, Spinner } from "./ui";

export function JobDescriptionPanel({
  jobId,
  confirmed,
  samples,
  onSaved,
}: {
  jobId: string;
  confirmed: boolean;
  samples: DemoSamples | null;
  onSaved: () => void;
}) {
  const { resource, reload } = useResource(
    (signal) =>
      getJobDescription(jobId, signal).catch((error: unknown) => {
        // A job with no description yet is the normal starting state, not a
        // failure worth showing as one.
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
    [jobId],
  );
  const save = useAction();
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);

  const existing = resource.state === "ready" ? resource.data : null;

  useEffect(() => {
    if (existing) setText(existing.raw_text);
  }, [existing]);

  const onSave = async () => {
    const ok = await save.run(() => setJobDescription(jobId, text.trim()));
    if (ok) {
      setEditing(false);
      reload();
      onSaved();
    }
  };

  if (resource.state === "loading") return <Spinner label="Loading job description…" />;
  if (resource.state === "error") return <ErrorState error={resource.error} onRetry={reload} />;

  const showEditor = editing || !existing;

  return (
    <section className="panel" aria-labelledby="jd-heading">
      <div className="panel__header">
        <h2 id="jd-heading">1 · Job description</h2>
        {existing && !editing ? (
          <button type="button" className="button button--quiet" onClick={() => setEditing(true)}>
            Replace
          </button>
        ) : null}
      </div>

      {showEditor ? (
        <>
          {samples ? (
            <p className="panel__hint">
              <button
                type="button"
                className="link-button"
                onClick={() => setText(samples.job_description)}
              >
                Use the sample job description
              </button>{" "}
              — synthetic, and the input the bundled offline fixtures were recorded against.
            </p>
          ) : null}

          <label className="field">
            <span className="field__label">Paste the job description</span>
            <textarea
              value={text}
              rows={12}
              maxLength={100000}
              placeholder="Paste the full job description here…"
              onChange={(event) => setText(event.target.value)}
            />
          </label>

          {existing ? (
            <Callout tone="warn" title="Replacing this text is not free">
              Requirements extracted from the old description are deleted and the job is
              unconfirmed, because they were read out of text that no longer exists. Anything
              already matched or scored against them is discarded too.
            </Callout>
          ) : null}

          <div className="button-row">
            <button
              type="button"
              className="button"
              onClick={onSave}
              disabled={save.busy || !text.trim()}
            >
              {save.busy ? "Saving…" : "Save description"}
            </button>
            {existing ? (
              <button
                type="button"
                className="button button--quiet"
                onClick={() => {
                  setText(existing.raw_text);
                  setEditing(false);
                  save.clearError();
                }}
              >
                Cancel
              </button>
            ) : null}
          </div>
          {save.error ? <ErrorState error={save.error} /> : null}
        </>
      ) : (
        <>
          <pre className="jd-preview">{existing.raw_text}</pre>
          {confirmed ? (
            <p className="panel__hint">
              The requirement set is confirmed. Replacing the description would discard it.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
