/**
 * Steps 3 and 4: upload CVs, screen them, read the ranked result.
 *
 * Screening is orchestrated here rather than in one backend call: for each
 * candidate the UI runs profile extraction, then matching, then scoring, and
 * reports where each one got to. That keeps the backend's stages separately
 * addressable — a candidate that fails at matching keeps its profile — and it
 * makes the pipeline visible to the person watching it, which is most of what
 * "processing status" is for here.
 *
 * Nothing is ever hidden by score. Candidates that failed, and candidates not
 * yet screened, are listed alongside the ranked ones with the reason why.
 */

import { useState } from "react";

import {
  extractProfile,
  getRanking,
  runMatching,
  runScoring,
  uploadCandidates,
  type JobRanking,
  type UploadOutcome,
} from "../api/client";
import {
  BAND_CAVEAT,
  SCORE_CAVEAT,
  STATUS_LABEL,
  asPercent,
  failureLabel,
  warningLabel,
} from "../display";
import { href } from "../hooks/useHashRoute";
import { useAction, useResource } from "../hooks/useResource";
import { Callout, EmptyState, ErrorState, Pill, ScoreBadge, Spinner } from "./ui";

type ScreenState = { done: number; total: number; failures: { name: string; message: string }[] };

export function CandidatesPanel({ jobId, confirmed }: { jobId: string; confirmed: boolean }) {
  const { resource, reload } = useResource((signal) => getRanking(jobId, signal), [jobId]);
  const upload = useAction();
  const [outcomes, setOutcomes] = useState<UploadOutcome[]>([]);
  const [screening, setScreening] = useState<ScreenState | null>(null);

  const onUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) return;
    const ok = await upload.run(async () => {
      const batch = await uploadCandidates(jobId, files);
      setOutcomes(batch.results);
    });
    event.target.value = "";
    if (ok) reload();
  };

  const ranking = resource.state === "ready" ? resource.data : null;
  const pending = ranking?.not_yet_scored ?? [];

  const onScreen = async () => {
    if (!ranking) return;
    const queue = ranking.not_yet_scored;
    const failures: { name: string; message: string }[] = [];
    setScreening({ done: 0, total: queue.length, failures });

    for (const [index, candidate] of queue.entries()) {
      const name =
        candidate.display_name ?? candidate.original_filename ?? candidate.candidate_id.slice(0, 8);
      try {
        await extractProfile(candidate.candidate_id);
        await runMatching(candidate.candidate_id);
        await runScoring(candidate.candidate_id);
      } catch (error: unknown) {
        failures.push({
          name,
          message: error instanceof Error ? error.message : "Unknown error",
        });
      }
      setScreening({ done: index + 1, total: queue.length, failures: [...failures] });
    }
    reload();
  };

  return (
    <>
      <section className="panel" aria-labelledby="upload-heading">
        <h2 id="upload-heading">3 · Candidates</h2>

        <label className="uploader">
          <input
            type="file"
            accept="application/pdf,.pdf"
            multiple
            onChange={onUpload}
            disabled={upload.busy}
          />
          <span className="uploader__label">{upload.busy ? "Uploading…" : "Choose CV PDFs"}</span>
          <span className="uploader__hint">
            PDF only, up to 10 MB and 20 pages each, 25 per batch. Scanned CVs are reported as
            unreadable rather than scored — there is no OCR.
          </span>
        </label>
        {upload.error ? <ErrorState error={upload.error} /> : null}

        {outcomes.length > 0 ? (
          <ul className="outcome-list">
            {outcomes.map((outcome) => (
              <li key={`${outcome.filename}-${outcome.candidate_id ?? "rejected"}`}>
                <span className="outcome__name">{outcome.filename}</span>
                {outcome.accepted && outcome.status !== "FAILED" ? (
                  <Pill tone="ok">Accepted</Pill>
                ) : (
                  <>
                    <Pill tone="warn">{outcome.accepted ? "Failed" : "Rejected"}</Pill>
                    <span className="outcome__detail">{outcome.detail}</span>
                  </>
                )}
              </li>
            ))}
          </ul>
        ) : null}

        {!confirmed ? (
          <Callout tone="warn" title="Screening is blocked until requirements are confirmed">
            You can upload CVs now, but nothing is matched or scored against a requirement set no
            one has agreed to. Confirm the requirements above to continue.
          </Callout>
        ) : null}

        {confirmed && pending.length > 0 ? (
          <div className="button-row">
            <button
              type="button"
              className="button"
              onClick={onScreen}
              disabled={screening !== null && screening.done < screening.total}
            >
              {screening && screening.done < screening.total
                ? `Screening ${screening.done + 1} of ${screening.total}…`
                : `Screen ${pending.length} candidate${pending.length === 1 ? "" : "s"}`}
            </button>
          </div>
        ) : null}

        {screening ? (
          <div className="progress" role="status">
            <div className="progress__bar">
              <div
                className="progress__fill"
                style={{
                  width: `${screening.total ? (screening.done / screening.total) * 100 : 100}%`,
                }}
              />
            </div>
            <p className="progress__label">
              {screening.done} of {screening.total} processed
              {screening.failures.length > 0
                ? ` · ${screening.failures.length} could not be screened`
                : ""}
            </p>
            {screening.failures.length > 0 ? (
              <ul className="failure-list">
                {screening.failures.map((failure) => (
                  <li key={failure.name}>
                    <strong>{failure.name}:</strong> {failure.message}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="panel" aria-labelledby="ranking-heading">
        <h2 id="ranking-heading">4 · Results</h2>
        {resource.state === "loading" ? <Spinner label="Loading results…" /> : null}
        {resource.state === "error" ? <ErrorState error={resource.error} onRetry={reload} /> : null}
        {ranking ? <RankingView ranking={ranking} /> : null}
      </section>
    </>
  );
}

function RankingView({ ranking }: { ranking: JobRanking }) {
  if (ranking.summary.total === 0) {
    return (
      <EmptyState title="No candidates yet">
        Upload CV PDFs above. Every file you upload is accounted for here, including any that fail
        to process.
      </EmptyState>
    );
  }

  return (
    <div className="stack">
      <p className="panel__hint">
        {ranking.summary.total} candidate{ranking.summary.total === 1 ? "" : "s"} ·{" "}
        {ranking.summary.ranked} ranked · {ranking.summary.not_yet_scored} not yet screened ·{" "}
        {ranking.summary.failed} failed
      </p>

      {ranking.ranked.length > 0 ? (
        <>
          <ol className="ranked-list">
            {ranking.ranked.map((entry) => (
              <li key={entry.candidate_id}>
                <a className="ranked-card" href={href.candidate(entry.candidate_id)}>
                  <span className="ranked-card__position">{entry.position}</span>
                  <span className="ranked-card__main">
                    <span className="ranked-card__name">
                      {entry.display_name ?? entry.original_filename ?? "Unnamed candidate"}
                    </span>
                    <span className="ranked-card__meta">
                      {entry.matched_count} requirement{entry.matched_count === 1 ? "" : "s"}{" "}
                      matched
                      {" · must-have coverage "}
                      {asPercent(entry.must_have_coverage)}
                    </span>
                    {entry.warnings.length > 0 ? (
                      <span className="ranked-card__warnings">
                        {entry.warnings.map((code) => (
                          <Pill key={code} tone="warn">
                            {warningLabel(code)}
                          </Pill>
                        ))}
                      </span>
                    ) : null}
                  </span>
                  <ScoreBadge score={entry.score} band={entry.band} capped={entry.capped} />
                </a>
              </li>
            ))}
          </ol>
          <p className="fineprint">
            {SCORE_CAVEAT} {BAND_CAVEAT}
          </p>
        </>
      ) : null}

      {ranking.not_yet_scored.length > 0 ? (
        <div>
          <h3 className="subhead">Not yet screened</h3>
          <ul className="plain-list">
            {ranking.not_yet_scored.map((entry) => (
              <li key={entry.candidate_id}>
                <span>{entry.display_name ?? entry.original_filename ?? entry.candidate_id}</span>
                <Pill>{STATUS_LABEL[entry.status]}</Pill>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {ranking.failed.length > 0 ? (
        <div>
          <h3 className="subhead">Could not be processed</h3>
          <p className="panel__hint">
            Listed rather than dropped: a file you uploaded should never disappear silently.
          </p>
          <ul className="plain-list">
            {ranking.failed.map((entry) => (
              <li key={entry.candidate_id}>
                <span>{entry.original_filename ?? entry.candidate_id}</span>
                <Pill tone="warn">{failureLabel(entry.failure_reason)}</Pill>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
