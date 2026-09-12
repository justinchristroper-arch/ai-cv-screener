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
  type Score,
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
  VERDICT_LABEL,
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
    NEEDS_REVIEW: matches?.results.filter((item) => item.verdict === "NEEDS_REVIEW") ?? [],
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

      {candidate.parsed && candidate.parsed.multi_column_pages.length > 0 ? (
        <Callout tone="warn" title="This CV is laid out in columns">
          <p>
            {candidate.parsed.multi_column_pages.length === 1
              ? `Page ${candidate.parsed.multi_column_pages[0]} has`
              : `Pages ${candidate.parsed.multi_column_pages.join(", ")} have`}{" "}
            text in two or more separated columns. Reading a PDF turns the page into one stream of
            lines, and for a column layout that stream can interleave sections that belong apart —
            an education line can land under the experience heading.
          </p>
          <p>
            Nothing here is corrected automatically, because guessing at the intended order would be
            a second way to get it wrong. The verdicts below still quote real lines of this
            document, but which section a line belongs to may be wrong, so this is a CV worth
            opening alongside them.
          </p>
        </Callout>
      ) : null}

      {/* Evidence first, then the number.
          The score badge is still in the header, because a reader arriving from
          a ranked list needs to know which candidate they are looking at. But
          the full arithmetic sits *below* the verdicts: a recruiter who reads
          the number first tends to read the evidence as a justification for it
          rather than as the thing the number came from. */}
      {matches ? (
        <section className="panel" aria-labelledby="verdicts-heading">
          <div className="panel__header">
            <h2 id="verdicts-heading">Requirement by requirement</h2>
            <p className="panel__hint">
              {matches.summary.matched} matched · {matches.summary.partial} partial ·{" "}
              {matches.summary.no_evidence} without evidence
              {matches.summary.needs_review > 0 ? (
                <> · {matches.summary.needs_review} could not be determined</>
              ) : null}{" "}
              · {matches.summary.decided_deterministically} decided by code
            </p>
          </div>

          <VerdictGroup title="Matched" items={grouped.MATCHED} />
          <VerdictGroup title="Partial" items={grouped.PARTIAL} />
          <VerdictGroup title="Gaps" items={grouped.NO_EVIDENCE} />
          {/* Last, and framed separately, because it is not a finding about the
              candidate at all — it is the screener reporting its own limit.
              Folding these in with the gaps would be the exact confusion that
              ADR-0012 added a fourth verdict to prevent. */}
          <VerdictGroup
            title="Could not be determined"
            items={grouped.NEEDS_REVIEW}
            note="Unresolved rather than unmet. These were left out of the score entirely — not counted as zeros — so they need a person to read the CV."
          />
        </section>
      ) : (
        <section className="panel">
          <h2>Requirement by requirement</h2>
          <EmptyState title="Not screened yet">
            This candidate has no verdicts. Run screening from the job page.
          </EmptyState>
        </section>
      )}

      {score ? <ScorePanel score={score} /> : null}

      {profile ? <ProfilePanel profile={profile} /> : null}
    </div>
  );
}

function ScorePanel({ score }: { score: Score }) {
  return (
    <section className="panel" aria-labelledby="score-heading">
      <h2 id="score-heading">Score</h2>

      {score.status === "UNDEFINED_NO_WEIGHT" ? (
        <Callout title="No score could be formed">
          This job has no weighted requirements, so there is nothing to compute. That is not a score
          of zero — nothing was asked of this candidate.
        </Callout>
      ) : score.status === "UNDEFINED_NO_DECIDABLE" ? (
        <Callout title="No score could be formed">
          <p>
            There were {score.contributions.length} criteri
            {score.contributions.length === 1 ? "on" : "a"} on this job, and the screening engine
            could not determine any of them from this CV. With nothing decidable there is no average
            to take.
          </p>
          <p>
            This is not a score of zero and it is not a finding about the candidate. Each criterion
            below says what stopped it; the CV itself is unchanged and still worth reading.
          </p>
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

          {score.capped ? <CappedCallout score={score} /> : null}

          {score.review_flag ? (
            <Callout title="The score does not cover every criterion">
              <p>
                {score.needs_review_count} of {score.contributions.length} criteria could not be
                determined from this CV
                {score.must_have_needs_review_count > 0 ? (
                  <>, including {score.must_have_needs_review_count} marked must-have</>
                ) : null}
                . They were excluded from the score on both sides of the average rather than counted
                as zeros, and their weight was not shared out among the others — so the remaining
                criteria are worth exactly what you set them to.
              </p>
              <p>
                The practical consequence is that this number is an average over{" "}
                {score.contributions.length - score.needs_review_count} criteri
                {score.contributions.length - score.needs_review_count === 1 ? "on" : "a"}, not over{" "}
                {score.contributions.length}. Comparing it with a candidate whose criteria all
                resolved is comparing two different questions.
              </p>
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
                      <td>{VERDICT_LABEL[item.verdict]}</td>
                      {/* An em dash rather than a 0, and the same in both
                          columns: an unresolved criterion is not worth nothing,
                          it is outside the sum. Its weight is printed above so
                          the exclusion is visible rather than implied. */}
                      <td>{item.verdict_value === null ? "—" : trimDecimal(item.verdict_value)}</td>
                      <td>{item.points === null ? "excluded" : trimDecimal(item.points)}</td>
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
              Matched counts 1.0, partial 0.5, no evidence 0. A criterion that could not be
              determined counts as nothing at all — it is left out of both the points and the total
              weight, which is why the totals below may be less than the weights above add up to.
              The score is {trimDecimal(score.weighted_sum ?? "0")} ÷{" "}
              {trimDecimal(score.total_weight ?? "0")} × 100, rounded. Every number here comes from
              stored verdicts and the weights you set — no model is involved in the arithmetic.
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

/**
 * Why the band was capped — and the two reasons are not the same thing.
 *
 * A must-have with no evidence says the CV shows nothing for it. A must-have
 * that could not be determined says the CV shows something the engine could not
 * read. Printing the first sentence for the second case would report our limit
 * as the candidate's gap, which is the failure this product is built against.
 */
function CappedCallout({ score }: { score: Score }) {
  const trigger = score.contributions.find(
    (item) => item.requirement_id === score.capped_by_requirement_id,
  );
  const unresolved = trigger?.verdict === "NEEDS_REVIEW";
  const named = score.capped_by_requirement_text ? (
    <>: “{score.capped_by_requirement_text}”</>
  ) : null;

  return (
    <Callout tone="warn" title="Band capped at Review">
      {unresolved ? (
        <>
          A must-have criterion could not be determined from this CV{named}. That is not the same as
          it being unmet — the band is capped because a clean label would overstate what was
          actually established, not because anything is missing. The score itself is unchanged, and
          the candidate is not rejected or hidden.
        </>
      ) : (
        <>
          A must-have requirement has no evidence in this CV{named}. The score itself is unchanged;
          a weighted average can look healthy while a hard requirement is missing entirely. The
          candidate is not rejected or hidden.
        </>
      )}
    </Callout>
  );
}

function VerdictGroup({
  title,
  items,
  note,
}: {
  title: string;
  items: MatchResult[];
  /** One line under the heading, for a group whose meaning is not obvious. */
  note?: string;
}) {
  if (items.length === 0) return null;

  return (
    <div className="verdict-group">
      <h3 className="subhead">
        {title} <span className="subhead__count">{items.length}</span>
      </h3>
      {note ? <p className="subhead__note">{note}</p> : null}
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
            {/* Only when the reason does not already carry the framing. An
                ordinary NO_EVIDENCE arrives with the server's own evidence-first
                wording; a downgraded one arrives with the reason its evidence
                was refused, which needs this added. Printing both said the same
                sentence twice and taught the reader to skip it. */}
            {item.verdict === "NO_EVIDENCE" && item.downgraded ? (
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
