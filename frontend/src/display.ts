/**
 * The words this product uses, in one place.
 *
 * Wording is a correctness concern here, not a styling one. The system reports
 * what a document contains; it does not judge a person. Centralising the labels
 * means "No evidence found in CV" cannot quietly become "Does not have this
 * skill" in one component while staying correct in another
 * (docs/product-spec.md section 10).
 *
 * The same applies to bands. A band is a reading aid with no empirical backing,
 * and the caveat travels with the label rather than being left to whoever
 * renders it.
 */

import type {
  CandidateStatus,
  MatchMethod,
  MatchVerdict,
  RecommendationBand,
  RequirementCategory,
} from "./api/client";

export const VERDICT_LABEL: Record<MatchVerdict, string> = {
  MATCHED: "Matched",
  PARTIAL: "Partial",
  // Never "does not have". A CV is a short, selective document, and its silence
  // is not proof of absence.
  NO_EVIDENCE: "No evidence found in CV",
};

export const VERDICT_MEANING: Record<MatchVerdict, string> = {
  MATCHED: "The CV contains direct evidence for this requirement.",
  PARTIAL: "The CV contains related evidence that does not fully meet this requirement.",
  NO_EVIDENCE:
    "This CV contains no verified evidence for this requirement. That is a statement about the document, not about the candidate.",
};

export const BAND_LABEL: Record<RecommendationBand, string> = {
  STRONG_MATCH: "Strong match",
  GOOD_MATCH: "Good match",
  REVIEW: "Review",
  LOW_MATCH: "Low match",
};

/** Shown wherever a band is shown. The band is never displayed alone. */
export const BAND_CAVEAT =
  "Bands are heuristic reading aids, not predictions. They are not a hiring decision, not a probability of success, and no candidate is filtered or hidden by one.";

export const SCORE_CAVEAT =
  "Scores are only comparable within this job — the requirements and weights differ between jobs.";

export const CATEGORY_LABEL: Record<RequirementCategory, string> = {
  EDUCATION: "Education",
  TECHNICAL_SKILL: "Technical skill",
  EXPERIENCE: "Experience",
  PROJECT: "Project",
  SOFT_SKILL_OTHER: "Soft skill / other",
};

export const METHOD_LABEL: Record<MatchMethod, string> = {
  DETERMINISTIC_EXACT: "Matched by exact skill rule",
  DETERMINISTIC_ALIAS: "Matched by skill alias rule",
  DETERMINISTIC_DURATION: "Decided by date arithmetic",
  LLM_SEMANTIC: "Judged by the language model",
  DOWNGRADED_UNVERIFIED: "Downgraded — proposed evidence was refused",
};

export const STATUS_LABEL: Record<CandidateStatus, string> = {
  UPLOADED: "Uploaded",
  PARSING: "Parsing",
  PARSED: "Parsed — not screened yet",
  EXTRACTING: "Extracting profile",
  EXTRACTED: "Profile extracted — not scored yet",
  SCORING: "Scoring",
  SCORED: "Screened",
  FAILED: "Failed",
};

export const FAILURE_LABEL: Record<string, string> = {
  CORRUPT_FILE: "The file could not be read as a PDF",
  NO_TEXT_LAYER: "No extractable text — most likely a scan. This system does not perform OCR",
  UNSUPPORTED_LANGUAGE: "Unsupported language",
  PARSE_TIMEOUT: "Parsing took too long",
  EXTRACTION_FAILED: "The profile could not be extracted",
  MATCHING_FAILED: "Matching could not be completed",
};

export const WARNING_LABEL: Record<string, string> = {
  MUST_HAVE_NOT_EVIDENCED:
    "A must-have requirement has no evidence in this CV, so the band is capped at Review",
  SCORE_UNDEFINED: "No score could be formed — the job has no weighted requirements",
  EVIDENCE_DOWNGRADED: "Proposed evidence was refused and the verdict was downgraded",
  INSTRUCTION_LIKE_TEXT_IN_CV:
    "This CV contains text that reads as an instruction rather than as a description of the candidate",
};

/** Human-readable label for a warning code, falling back to the code itself. */
export function warningLabel(code: string): string {
  return WARNING_LABEL[code] ?? code;
}

export function failureLabel(reason: string | null): string {
  if (!reason) return "Processing failed";
  return FAILURE_LABEL[reason] ?? reason;
}

/**
 * Format a decimal string the backend sent as a percentage.
 *
 * The value arrives as a string because it is a NUMERIC column; parsing it to a
 * float here is safe only because the result is immediately rounded for display
 * and never fed back into arithmetic.
 */
export function asPercent(value: string | null): string {
  if (value === null) return "—";
  const parsed = Number.parseFloat(value);
  if (Number.isNaN(parsed)) return "—";
  return `${Math.round(parsed * 100)}%`;
}

/** Trim the trailing zeros off a NUMERIC string: "3.00" reads better as "3". */
export function trimDecimal(value: string): string {
  if (!value.includes(".")) return value;
  return value.replace(/\.?0+$/, "") || "0";
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
