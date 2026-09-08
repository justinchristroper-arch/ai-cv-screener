/**
 * Step 1: the screening criteria.
 *
 * Not "the job description". A recruiter often does not have one — what they
 * have is a few lines in their head about who they want, in whatever language
 * they think in. Asking for a formal document before the product will do
 * anything is a barrier the product had no reason to put there, so the box
 * accepts whatever they type and the copy says so.
 *
 * Two things are stated *before* the button rather than after it, because both
 * are things a person should not have to discover by failing:
 *
 * - in demo mode, only the sample briefs have recorded AI responses;
 * - criteria that name a personal characteristic will be refused at
 *   confirmation, so they are flagged here, three steps earlier.
 *
 * Replacing saved text also has consequences — extracted requirements are
 * deleted and the job is unconfirmed — and that is stated before the button too.
 */

import { useEffect, useState } from "react";

import {
  ApiError,
  getJobDescription,
  setJobDescription,
  type DemoSamples,
  type SampleCriteria,
} from "../api/client";
import { useAction, useResource } from "../hooks/useResource";
import { Callout, ErrorState, Spinner } from "./ui";

function ProtectedAttributeCallout({ labels }: { labels: string[] }) {
  return (
    <Callout tone="warn" title="These criteria ask about a personal characteristic">
      <p>
        This text mentions {labels.join(", ")}. Requirements about a person rather than about their
        work cannot be confirmed here, so the requirement set will be refused until they are removed
        or reworded. Nothing has been changed &mdash; your text is stored exactly as you typed it.
      </p>
      <p>
        The reason is not only legal caution: this tool holds no such information about anybody. It
        never extracts age, gender, marital status, religion, ethnicity, nationality, appearance or
        health from a CV, so a criterion about one could never be answered from evidence.
      </p>
    </Callout>
  );
}

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
        // A job with no criteria yet is the normal starting state, not a
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

  if (resource.state === "loading") return <Spinner label="Loading screening criteria…" />;
  if (resource.state === "error") return <ErrorState error={resource.error} onRetry={reload} />;

  const showEditor = editing || !existing;
  const sampleCriteria: SampleCriteria[] = samples?.criteria ?? [];
  const matchesASample = sampleCriteria.some((item) => item.text === existing?.raw_text);
  const flagLabels = [...new Set((existing?.protected_attribute_flags ?? []).map((f) => f.label))];

  const use = (item: SampleCriteria) => {
    setText(item.text);
    setEditing(true);
  };

  return (
    <section className="panel" aria-labelledby="jd-heading">
      <div className="panel__header">
        <h2 id="jd-heading">1 · Screening criteria</h2>
        {existing && !editing ? (
          <button type="button" className="button button--quiet" onClick={() => setEditing(true)}>
            Replace
          </button>
        ) : null}
      </div>

      {showEditor ? (
        <>
          <p className="panel__hint">
            Write what you are looking for, in your own words and your own language. A full job
            description works; so does &ldquo;minimal S1, IPK di atas 3, bisa bahasa Inggris,
            pengalaman Python minimal 2 tahun&rdquo;. You will review every requirement before
            anything is screened.
          </p>

          {sampleCriteria.length > 0 ? (
            <div className="samples">
              <p className="samples__title">
                Demo mode — these are the briefs this build has recorded AI responses for:
              </p>
              <ul className="samples__list">
                {sampleCriteria.map((item) => (
                  <li key={item.id} className="samples__item">
                    <div>
                      <span className="samples__label">{item.label}</span>
                      <span className="samples__language">{item.language}</span>
                      <p className="samples__hint">{item.demonstrates}</p>
                    </div>
                    <button
                      type="button"
                      className="button button--quiet"
                      onClick={() => use(item)}
                    >
                      Use this
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <label className="field">
            <span className="field__label">Your screening criteria</span>
            <textarea
              value={text}
              rows={12}
              maxLength={100000}
              placeholder={
                "Paste a job description, or just write what you need. For example:\n\n" +
                "Saya cari backend engineer yang pernah kerja dengan Python dan PostgreSQL, " +
                "minimal 2 tahun pengalaman, kalau pernah AI/ML lebih bagus."
              }
              onChange={(event) => setText(event.target.value)}
            />
          </label>

          {existing ? (
            <Callout tone="warn" title="Replacing this text is not free">
              Requirements extracted from the old criteria are deleted and the job is unconfirmed,
              because they were read out of text that no longer exists. Anything already matched or
              scored against them is discarded too.
            </Callout>
          ) : null}

          <div className="button-row">
            <button
              type="button"
              className="button"
              onClick={onSave}
              disabled={save.busy || !text.trim()}
            >
              {save.busy ? "Saving…" : "Save criteria"}
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

          {flagLabels.length > 0 ? <ProtectedAttributeCallout labels={flagLabels} /> : null}

          {/* The same treatment a CV gets: flagged, kept, never obeyed. Shown
              here because this is where a person can still act on it — before
              requirements are extracted from the text and confirmed. */}
          {existing.injection_flag_count > 0 ? (
            <Callout tone="warn" title="This text contains instruction-like passages">
              {existing.injection_flag_count} passage
              {existing.injection_flag_count === 1 ? "" : "s"} here read as instructions addressed
              to the system rather than as a statement of what the role needs. The text is flagged
              and kept, never removed and never acted on, and you still review and confirm every
              requirement before any candidate is screened against it.
            </Callout>
          ) : null}

          {/* Said here, before the Extract button further down the page, rather
              than after the user presses it and gets a failure. Demo mode
              replays AI responses recorded in advance, so it can only answer
              for the briefs they were recorded against — a real limitation,
              not a fault, and one the user should not have to discover. */}
          {sampleCriteria.length > 0 && !matchesASample ? (
            <Callout tone="warn" title="These criteria cannot be analysed in demo mode">
              <p>
                The application is running in demo mode, which replays AI responses recorded in
                advance. It can only extract requirements from one of the sample briefs &mdash; your
                own text would need an AI provider configured, and nothing has been sent to a model.
              </p>
              <p className="samples__inline">
                Try one of these instead:{" "}
                {sampleCriteria.map((item, index) => (
                  <span key={item.id}>
                    {index > 0 ? " · " : null}
                    <button type="button" className="link-button" onClick={() => use(item)}>
                      {item.label}
                    </button>
                  </span>
                ))}
              </p>
              <p>Your text stays until you save.</p>
            </Callout>
          ) : null}

          {confirmed ? (
            <p className="panel__hint">
              The requirement set is confirmed. Replacing these criteria would discard it.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
