/**
 * The small shared pieces: states, badges, pills.
 *
 * Every one of these renders its content as **text**. There is no
 * `dangerouslySetInnerHTML` anywhere in this application, which is the XSS
 * control for CV-derived strings — a CV containing `<script>` renders inertly
 * because React escapes it (docs/architecture.md section 10).
 */

import type { ReactNode } from "react";

import type { MatchVerdict, RecommendationBand } from "../api/client";
import { BAND_CAVEAT, BAND_LABEL, VERDICT_LABEL, VERDICT_MEANING } from "../display";

// --------------------------------------------------------------------------
// Loading, error and empty states
// --------------------------------------------------------------------------

export function Spinner({ label }: { label: string }) {
  return (
    <p className="state state--loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function ErrorState({
  error,
  onRetry,
  hint,
}: {
  error: Error;
  onRetry?: () => void;
  hint?: ReactNode;
}) {
  return (
    <div className="state state--error" role="alert">
      <p className="state__title">{error.message}</p>
      {hint ? <p className="state__hint">{hint}</p> : null}
      {onRetry ? (
        <button type="button" className="button button--quiet" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state--empty">
      <p className="state__title">{title}</p>
      {children ? <div className="state__hint">{children}</div> : null}
    </div>
  );
}

// --------------------------------------------------------------------------
// Score and band
// --------------------------------------------------------------------------

const BAND_CLASS: Record<RecommendationBand, string> = {
  STRONG_MATCH: "band--strong",
  GOOD_MATCH: "band--good",
  REVIEW: "band--review",
  LOW_MATCH: "band--low",
};

/**
 * A score and its band, always together.
 *
 * product-spec section 12 is explicit: the band is displayed next to its score
 * and its must-have coverage, never alone, and the UI states that bands are
 * heuristic wherever they are shown.
 */
export function ScoreBadge({
  score,
  band,
  capped,
  size = "normal",
}: {
  score: number | null;
  band: RecommendationBand | null;
  capped?: boolean;
  size?: "normal" | "large";
}) {
  const className = [
    "score-badge",
    size === "large" ? "score-badge--large" : "",
    band ? BAND_CLASS[band] : "band--undefined",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={className} title={BAND_CAVEAT}>
      <span className="score-badge__number">{score === null ? "—" : score}</span>
      <span className="score-badge__band">
        {band ? BAND_LABEL[band] : "No score"}
        {capped ? " (capped)" : ""}
      </span>
    </span>
  );
}

// --------------------------------------------------------------------------
// Verdicts
// --------------------------------------------------------------------------

const VERDICT_CLASS: Record<MatchVerdict, string> = {
  MATCHED: "verdict--matched",
  PARTIAL: "verdict--partial",
  NO_EVIDENCE: "verdict--none",
};

export function VerdictPill({ verdict }: { verdict: MatchVerdict }) {
  return (
    <span className={`verdict ${VERDICT_CLASS[verdict]}`} title={VERDICT_MEANING[verdict]}>
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`pill pill--${tone}`}>{children}</span>;
}

/**
 * A quotation taken from a CV.
 *
 * Shown with its verification status, because an unverified quote is exactly
 * the thing a reader must not mistake for evidence.
 */
export function EvidenceQuote({
  text,
  page,
  status,
}: {
  text: string;
  page: number | null;
  status: string;
}) {
  const unverified = status === "UNVERIFIED";
  return (
    <figure className={`evidence ${unverified ? "evidence--unverified" : ""}`}>
      <blockquote>{text}</blockquote>
      <figcaption>
        {unverified
          ? "Not found in the CV — refused as evidence"
          : `Verified in the CV${page !== null ? `, page ${page}` : ""}`}
      </figcaption>
    </figure>
  );
}

export function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warn";
  title?: string;
  children: ReactNode;
}) {
  return (
    <div className={`callout callout--${tone}`}>
      {title ? <p className="callout__title">{title}</p> : null}
      <div className="callout__body">{children}</div>
    </div>
  );
}
