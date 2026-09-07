/**
 * One candidate: the score, how it was arrived at, and the evidence behind it.
 *
 * The page is arranged so the evidence is never further away than the claim it
 * supports. A verdict without its quote is exactly the opaque output this
 * project exists not to produce, so every matched or partial requirement shows
 * the passage it rests on, and every gap says plainly that it is a statement
 * about the document.
 *
 * All CV-derived text — quotes, role titles, skill names — is rendered as React
 * text, never as HTML. A CV containing markup renders inertly.
 */

import {
  ApiError,
  getCandidate,
  getMatches,
  getProfile,
  getScore,
  type MatchResult,
} from "../api/client";
import {
  Callout,
  EmptyState,
  ErrorState,
  EvidenceQuote,
  Pill,
  ScoreBadge,
  Spinner,
  VerdictPill,
} from "../components/ui";
import {
  BAND_CAVEAT,
  CATEGORY_LABEL,
  METHOD_LABEL,
  SCORE_CAVEAT,
  STATUS_LABEL,
  VERDICT_MEANING,
  asPercent,
  failureLabel,
  formatDate,
  trimDecimal,
} from "../display";
import { href } from "../hooks/useHashRoute";
import { useResource } from "../hooks/useResource";

/** 404 means "not produced yet", which is a state rather than a failure. */
function optional<T>(promise: Promise<T>): Promise<T | null> {
  return promise.catch((error: unknown) => {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  });
}

export function CandidatePage({ candidateId }: { candidateId: string }) {
  const { resource, reload } = useResource(
    async (signal) => {
      const candidate = await getCandidate(candidateId, signal);
      const [score, matches, profile] = await Promise.all([
        optional(getScore(candidateId, signal)),
        optional(getMatches(candidateId, signal)),
        optional(getProfile(candidateId, signal)),
      ]);
      return { candidate, score, matches, profile };
    },
    [candidateId],
  );

  if (resource.state === "loading") return <Spinner label="Loading candidate…" />;
  if (resource.state === "error") {
    return (
      <ErrorState
        error={resource.error}
        onRetry={reload}
        hint={<a href={href.jobs()}>Back to all jobs</a>}
      />
    );
  }

  const { candidate, score, matches, profile } = resource.data;
  const name = candidate.display_name ?? candidate.document?.original_filename ?? "Candidate";

  const grouped = {
    MATCHED: matches?.results.filter((item) => item.verdict === "MATCHED") ?? [],
    PARTIAL: matches?.results.filter((item) => item.verdict === "PARTIAL") ?? [],
    NO_EVIDENCE: matches?.results.filter((item) => item.verdict === "NO_EVIDENCE") ?? [],
  };

  return (
    <div className="stack">
      <nav className="crumbs">
        <a href={href.jobs()}>All jobs</a>
        <span aria-hidden="true">›</span>
        <a href={href.job(candidate.job_id)}>Job</a>
        <span aria-hidden="true">›</span>
        <span>{name}</span>
      </nav>

      <header className="page-header">
        <div>
          <h1>{name}</h1>
          <p className="page-header__meta">
            {candidate.document?.original_filename ?? "No document"}
            {candidate.parsed
              ? ` · ${candidate.parsed.page_count} page${candidate.parsed.page_count === 1 ? "" : "s"}`
              : null}
            {" · "}
            {STATUS_LABEL[candidate.status]}
          </p>
        </div>
        {score ? (
          <ScoreBadge score={score.score} band={score.band} capped={score.capped} size="large" />
        ) : null}
      </header>

      {candidate.status === "FAILED" ? (
        <Callout tone="warn" title="This candidate could not be processed">
          {failureLabel(candidate.failure_reason)}
          {candidate.failure_detail ? <> — {candidate.failure_detail}</> : null}
        </Callout>
      ) : null}

      {candidate.parsed && candidate.parsed.injection_flag_count > 0 ? (
        <Callout tone="warn" title="This CV contains instruction-like text">
          {candidate.parsed.injection_flag_count} passage
          {candidate.parsed.injection_flag_count === 1 ? "" : "s"} in this document read as
          instructions addressed to the system rather than as a description of the candidate. The
          text is flagged and kept, never removed, and it cannot be used as evidence for any
          requirement.
        </Callout>
      ) : null}

      {score ? <ScorePanel score={score} /> : null}

      {matches ? (
        <section className="panel" aria-labelledby="verdicts-heading">
          <div className="panel__header">
            <h2 id="verdicts-heading">Requirement by requirement</h2>
            <p className="panel__hint">
              {matches.summary.matched} matched · {matches.summary.partial} partial ·{" "}
              {matches.summary.no_evidence} without evidence ·{" "}
              {matches.summary.decided_deterministically} decided by code
            </p>
          </div>

          <VerdictGroup title="Matched" items={grouped.MATCHED} />
          <VerdictGroup title="Partial" items={grouped.PARTIAL} />
          <VerdictGroup title="Gaps" items={grouped.NO_EVIDENCE} />
        </section>
      ) : (
        <section className="panel">
          <h2>Requirement by requirement</h2>
          <EmptyState title="Not screened yet">
            This candidate has no verdicts. Run screening from the job page.
          </EmptyState>
        </section>
      )}

      {profile ? <ProfilePanel profile={profile} /> : null}
    </div>
  );
}

function ScorePanel({ score }: { score: NonNullable<Awaited<ReturnType<typeof getScore>>> }) {
  return (
    <section className="panel" aria-labelledby="score-heading">
      <h2 id="score-heading">Score</h2>

      {score.status === "UNDEFINED_NO_WEIGHT" ? (
        <Callout title="No score could be formed">
          This job has no weighted requirements, so there is nothing to compute. That is not a score
          of zero — nothing was asked of this candidate.
        </Callout>
      ) : (
        <>
          <dl className="stat-row">
            <div>
              <dt>Score</dt>
              <dd>{score.score} / 100</dd>
            </div>
            <div>
              <dt>Must-have coverage</dt>
              <dd>{asPercent(score.must_have_coverage)}</dd>
            </div>
            <div>
              <dt>Weighted points</dt>
              <dd>
                {trimDecimal(score.weighted_sum ?? "0")} of {trimDecimal(score.total_weight ?? "0")}
              </dd>
            </div>
            <div>
              <dt>Computed</dt>
              <dd>{formatDate(score.computed_at)}</dd>
            </div>
          </dl>

          {score.capped ? (
            <Callout tone="warn" title="Band capped at Review">
              A must-have requirement has no evidence in this CV
              {score.capped_by_requirement_text ? (
                <>: “{score.capped_by_requirement_text}”</>
              ) : null}
              . The score itself is unchanged; a weighted average can look healthy while a hard
              requirement is missing entirely. The candidate is not rejected or hidden.
            </Callout>
          ) : null}

          <details className="breakdown">
            <summary>Show the arithmetic</summary>
            <div className="table-scroll">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th scope="col">Requirement</th>
                    <th scope="col">Weight</th>
                    <th scope="col">Verdict</th>
                    <th scope="col">Value</th>
                    <th scope="col">Points</th>
                  </tr>
                </thead>
                <tbody>
                  {score.contributions.map((item) => (
                    <tr key={item.requirement_id}>
                      <td>
                        {item.requirement_text}
                        {item.must_have ? <Pill tone="must">Must have</Pill> : null}
                      </td>
                      <td>{trimDecimal(item.weight)}</td>
                      <td>
                        {item.verdict === "NO_EVIDENCE"
                          ? "No evidence"
                          : item.verdict === "PARTIAL"
                            ? "Partial"
                            : "Matched"}
                      </td>
                      <td>{trimDecimal(item.verdict_value)}</td>
                      <td>{trimDecimal(item.points)}</td>
                    </tr>
                  ))}
                  <tr className="table__total">
                    <td>Total</td>
                    <td>{trimDecimal(score.total_weight ?? "0")}</td>
                    <td colSpan={2} />
                    <td>{trimDecimal(score.weighted_sum ?? "0")}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="fineprint">
              Matched counts 1.0, partial 0.5, no evidence 0. The score is{" "}
              {trimDecimal(score.weighted_sum ?? "0")} ÷ {trimDecimal(score.total_weight ?? "0")} ×
              100, rounded. Every number here comes from stored verdicts and the weights you set —
              no model is involved in the arithmetic.
            </p>
          </details>
        </>
      )}

      <p className="fineprint">
        {SCORE_CAVEAT} {BAND_CAVEAT}
      </p>
    </section>
  );
}

function VerdictGroup({ title, items }: { title: string; items: MatchResult[] }) {
  if (items.length === 0) return null;

  return (
    <div className="verdict-group">
      <h3 className="subhead">
        {title} <span className="subhead__count">{items.length}</span>
      </h3>
      <ul className="verdict-list">
        {items.map((item) => (
          <li key={item.requirement_id} className="verdict-item">
            <div className="verdict-item__head">
              <span className="verdict-item__text">{item.requirement_text}</span>
              <span className="verdict-item__tags">
                {item.must_have ? <Pill tone="must">Must have</Pill> : null}
                <Pill>{CATEGORY_LABEL[item.category]}</Pill>
                <VerdictPill verdict={item.verdict} />
              </span>
            </div>
            <p className="verdict-item__reason">{item.reason}</p>
            {item.evidence ? (
              <EvidenceQuote
                text={item.evidence.quoted_text}
                page={item.evidence.page_number}
                status={item.evidence.verification_status}
              />
            ) : null}
            <p className="verdict-item__method">
              {METHOD_LABEL[item.decided_by]}
              {item.downgraded && item.raw_verdict ? (
                <>
                  {" "}
                  · the model proposed {item.raw_verdict.toLowerCase().replace("_", " ")}, which was
                  refused
                </>
              ) : null}
            </p>
            {item.verdict === "NO_EVIDENCE" ? (
              <p className="verdict-item__note">{VERDICT_MEANING.NO_EVIDENCE}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProfilePanel({
  profile,
}: {
  profile: NonNullable<Awaited<ReturnType<typeof getProfile>>>;
}) {
  const { evidence_summary: summary } = profile;

  return (
    <section className="panel" aria-labelledby="profile-heading">
      <div className="panel__header">
        <h2 id="profile-heading">What the CV states</h2>
        <p className="panel__hint">
          {summary.verified} of {summary.items} items verified against the document
        </p>
      </div>

      <Callout title="Only job-relevant content is extracted">
        There is no field anywhere in this profile for a name, age, date of birth, gender,
        nationality, photo, marital status, address, phone number or email — so none of it can reach
        matching or scoring, whatever the CV happens to print.
      </Callout>

      {profile.skills.length > 0 ? (
        <div className="profile-block">
          <h3 className="subhead">Skills</h3>
          <p className="chip-row">
            {profile.skills.map((skill) => (
              <span key={skill.id} className="chip">
                {skill.raw_name}
              </span>
            ))}
          </p>
        </div>
      ) : null}

      {profile.experience.length > 0 ? (
        <div className="profile-block">
          <h3 className="subhead">Experience</h3>
          <ul className="plain-list plain-list--stacked">
            {profile.experience.map((role) => (
              <li key={role.id}>
                <strong>{role.role_title}</strong>
                {role.organization ? <> — {role.organization}</> : null}
                <span className="muted">
                  {" "}
                  {role.start_date ?? "?"} to {role.is_current ? "present" : (role.end_date ?? "?")}
                </span>
                {role.description ? <p className="muted">{role.description}</p> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {profile.education.length > 0 ? (
        <div className="profile-block">
          <h3 className="subhead">Education</h3>
          <ul className="plain-list plain-list--stacked">
            {profile.education.map((item) => (
              <li key={item.id}>
                <strong>
                  {[item.degree, item.field_of_study].filter(Boolean).join(" ") || "Qualification"}
                </strong>
                {item.institution ? <> — {item.institution}</> : null}
                {item.completion_year ? (
                  <span className="muted"> ({item.completion_year})</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {profile.projects.length > 0 ? (
        <div className="profile-block">
          <h3 className="subhead">Projects</h3>
          <ul className="plain-list plain-list--stacked">
            {profile.projects.map((project) => (
              <li key={project.id}>
                <strong>{project.name}</strong>
                {project.description ? <p className="muted">{project.description}</p> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
