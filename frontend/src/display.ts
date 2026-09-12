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
  RequirementSpecType,
} from "./api/client";

export const VERDICT_LABEL: Record<MatchVerdict, string> = {
  MATCHED: "Matched",
  PARTIAL: "Partial",
  // Never "does not have". A CV is a short, selective document, and its silence
  // is not proof of absence.
  NO_EVIDENCE: "No evidence found in CV",
  // Distinct from "no evidence" on purpose. This one is about the screener, not
  // the document: something is written here that we could not read safely.
  NEEDS_REVIEW: "Needs review",
};

export const VERDICT_MEANING: Record<MatchVerdict, string> = {
  MATCHED: "The CV contains direct evidence for this requirement.",
  PARTIAL: "The CV contains related evidence that does not fully meet this requirement.",
  NO_EVIDENCE:
    "This CV contains no verified evidence for this requirement. That is a statement about the document, not about the candidate.",
  NEEDS_REVIEW:
    "The screening engine could not determine this criterion confidently, so it was left for a person to read. It is excluded from the score entirely rather than counted as a zero — this says nothing about the candidate either way.",
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

/**
 * What each structured criterion type means, in one line.
 *
 * Shown next to the field the recruiter is filling in, because the honest
 * limits of a criterion — presence rather than proficiency, a stated scale
 * rather than an assumed one — are easiest to accept while choosing it.
 */
export const SPEC_TYPE_HINT: Record<RequirementSpecType, string> = {
  EDUCATION_MIN: "Matched when the CV states a qualification at this level or above.",
  GPA_MIN:
    "You state the scale. A grade written without one cannot be compared, and is left for review rather than guessed at.",
  EXPERIENCE_MIN: "Measured from dated entries in the CV. Overlapping roles are counted once.",
  SKILL: "Chosen from the supported list, so the result means the same thing every time.",
  INTERNSHIP_MIN: "Leave the duration empty to screen for having done one at all.",
  LANGUAGE_PRESENT:
    "Presence only. This screener never infers how well someone speaks a language from a CV.",
  EXPERIENCE_IN_FIELD:
    "Counts only the roles whose own CV entry evidences this skill, so unrelated experience does not answer it.",
  CERTIFICATION_PRESENT:
    "Presence only. The CV has to claim the credential — a date is never compared, and one certificate never stands in for another.",
};

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
  DETERMINISTIC_STRUCTURED: "Decided by the structured screening rules",
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
  MUST_HAVE_NEEDS_REVIEW:
    "A must-have criterion could not be determined from this CV, so the band is capped at Review. That is not the same as it being missing",
  SCORE_UNDEFINED: "No score could be formed — the job has no weighted requirements",
  NO_DECIDABLE_CRITERIA:
    "No score could be formed — none of the criteria could be determined from this CV",
  CRITERIA_NEED_REVIEW:
    "Some criteria could not be determined and were left out of the score entirely, rather than counted as zeros",
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

// --------------------------------------------------------------------------
// Errors, in language a recruiter can act on
// --------------------------------------------------------------------------

/**
 * Turn a failure into something a person can understand and act on.
 *
 * The backend's own wording is written for an operator reading a log — it names
 * fixtures, prompt versions and input hashes, which are meaningless to a
 * recruiter and read as if the product were broken. The stable `code` is the
 * contract, so the translation keys on that and never on the prose.
 *
 * The demo-mode case is the one that matters. It is not a failure of the
 * product and it is not something the user did wrong: demo mode replays
 * recorded AI responses, so it can only answer for the documents those
 * responses were recorded against. The message says that, and is careful not to
 * suggest the AI looked at the custom input and declined — nothing was read.
 */
export function describeError(error: unknown): string {
  const code = (error as { code?: string } | null)?.code;
  const message = error instanceof Error ? error.message : "Something went wrong.";

  if (code === "llm_unavailable") {
    return (
      "This job description is not part of the demo data set, so it was not sent " +
      "to an AI model and nothing was read from it. Demo mode replays AI " +
      "responses recorded in advance, which means it can only extract " +
      "requirements from the sample job description. Load the sample below to " +
      "see the full workflow, or run the application with an AI provider " +
      "configured to use your own text."
    );
  }
  if (code === "requirements_not_confirmed") {
    return "The requirements for this job have not been confirmed yet, so nothing can be screened against them.";
  }
  if (code === "extraction_failed") {
    return "The AI model returned something this application could not use, and nothing was saved. Try again.";
  }
  return message;
}

/** True when a failure is the demo-mode limitation rather than a real fault. */
export function isDemoLimitation(error: unknown): boolean {
  return (error as { code?: string } | null)?.code === "llm_unavailable";
}
