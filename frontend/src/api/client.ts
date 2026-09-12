/**
 * Backend API client.
 *
 * One module, one job: turn the backend's HTTP contract into typed functions.
 * The types below mirror `backend/app/schemas/api/` — where the backend sends a
 * NUMERIC column it arrives as a **string** (`weight`, `score_raw`,
 * `must_have_coverage`), because JSON numbers cannot carry decimal precision
 * safely. They are kept as strings here and formatted for display rather than
 * being parsed into floats that would drift.
 *
 * The base URL comes from VITE_API_BASE_URL so a deployed frontend can point at
 * a different origin without a source change. Nothing secret is read here:
 * everything in `import.meta.env` is baked into the browser bundle, so provider
 * keys stay server-side.
 */

/** Strip trailing slashes so path joining is predictable. Exported for testing. */
export function normalizeBaseUrl(raw: string): string {
  return raw.replace(/\/+$/, "");
}

export const API_BASE_URL = normalizeBaseUrl(
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
);

// --------------------------------------------------------------------------
// Errors
// --------------------------------------------------------------------------

/**
 * A failed request.
 *
 * `code` is the backend's stable identifier (`not_found`, `conflict`,
 * `requirements_not_confirmed`, …). The UI branches on that, never on the prose,
 * so a reworded message cannot change behaviour.
 */
export class ApiError extends Error {
  readonly status?: number;
  readonly code?: string;

  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface ErrorBody {
  code?: string;
  message?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    // A network-level failure. Deliberately not surfacing the raw error text,
    // which varies by browser and says nothing useful to the user.
    throw new ApiError(`Could not reach the backend at ${API_BASE_URL}`);
  }

  if (!response.ok) {
    let body: ErrorBody = {};
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      // A non-JSON error body. The status is still worth reporting.
    }
    throw new ApiError(
      body.message ?? `Backend returned HTTP ${response.status}`,
      response.status,
      body.code,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function jsonRequest<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

// --------------------------------------------------------------------------
// Shared enumerations, mirroring backend/app/core/enums.py
// --------------------------------------------------------------------------

export type RequirementCategory =
  "EDUCATION" | "TECHNICAL_SKILL" | "EXPERIENCE" | "PROJECT" | "SOFT_SKILL_OTHER";

export type CandidateStatus =
  "UPLOADED" | "PARSING" | "PARSED" | "EXTRACTING" | "EXTRACTED" | "SCORING" | "SCORED" | "FAILED";

export type MatchVerdict = "MATCHED" | "PARTIAL" | "NO_EVIDENCE" | "NEEDS_REVIEW";

export type MatchMethod =
  | "DETERMINISTIC_EXACT"
  | "DETERMINISTIC_ALIAS"
  | "DETERMINISTIC_DURATION"
  | "DETERMINISTIC_STRUCTURED"
  | "LLM_SEMANTIC"
  | "DOWNGRADED_UNVERIFIED";

/**
 * The structured screening criteria: the six from ADR-0012, plus
 * EXPERIENCE_IN_FIELD, which is the only one carrying a subject and a
 * threshold together.
 */
export type RequirementSpecType =
  | "EDUCATION_MIN"
  | "GPA_MIN"
  | "EXPERIENCE_MIN"
  | "SKILL"
  | "INTERNSHIP_MIN"
  | "LANGUAGE_PRESENT"
  | "EXPERIENCE_IN_FIELD"
  | "CERTIFICATION_PRESENT";

export type EvidenceVerification = "VERIFIED_EXACT" | "VERIFIED_NORMALIZED" | "UNVERIFIED";

export type RecommendationBand = "STRONG_MATCH" | "GOOD_MATCH" | "REVIEW" | "LOW_MATCH";

export type ScoreStatus = "COMPUTED" | "UNDEFINED_NO_WEIGHT" | "UNDEFINED_NO_DECIDABLE";

// --------------------------------------------------------------------------
// Health
// --------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  version: string;
  app_env: string;
  demo_mode: boolean;
  /** "ollama" — a model on the server's own machine — or "anthropic". */
  llm_provider: string;
  llm_model: string;
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal });
}

// --------------------------------------------------------------------------
// Jobs
// --------------------------------------------------------------------------

export interface Job {
  id: string;
  title: string;
  requirements_confirmed_at: string | null;
  has_description: boolean;
  requirement_count: number;
  created_at: string;
  updated_at: string;
}

export interface JobDescription {
  id: string;
  job_id: string;
  source_type: string;
  source_filename: string | null;
  raw_text: string;
  text_sha256: string;
  created_at: string;
  injection_flag_count: number;
  protected_attribute_flags: ProtectedAttributeFlag[];
}

export function listJobs(signal?: AbortSignal): Promise<Job[]> {
  return request<Job[]>("/api/jobs", { signal });
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}`, { signal });
}

export function createJob(title: string): Promise<Job> {
  return jsonRequest<Job>("/api/jobs", "POST", { title });
}

export function getJobDescription(jobId: string, signal?: AbortSignal): Promise<JobDescription> {
  return request<JobDescription>(`/api/jobs/${jobId}/description`, { signal });
}

export function setJobDescription(jobId: string, rawText: string): Promise<JobDescription> {
  return jsonRequest<JobDescription>(`/api/jobs/${jobId}/description`, "PUT", {
    raw_text: rawText,
    source_type: "PASTED",
  });
}

// --------------------------------------------------------------------------
// Requirements
// --------------------------------------------------------------------------

export interface Requirement {
  id: string;
  job_id: string;
  text: string;
  category: RequirementCategory;
  must_have: boolean;
  /** NUMERIC(5,2) — a string, so the decimal is exact. */
  weight: string;
  display_order: number;
  origin: "LLM_EXTRACTED" | "HR_ADDED";
  /**
   * Set on a structured criterion, null on a legacy free-text row. Its presence
   * is what tells the UI whether this row was screened by the deterministic
   * engine or by the model.
   */
  spec_type: RequirementSpecType | null;
  subject: string | null;
  /** NUMERIC — a string. Months for a duration, a grade for GPA. */
  threshold_value: string | null;
  threshold_scale: string | null;
  proposed_text: string | null;
  proposed_category: RequirementCategory | null;
  proposed_must_have: boolean | null;
  created_at: string;
  updated_at: string;
  /**
   * Protected personal characteristics this requirement's wording names.
   * Non-empty means the requirement set cannot be confirmed until it is removed
   * or reworded, so the UI says so on the row rather than at the wall.
   */
  protected_attribute_flags: ProtectedAttributeFlag[];
}

export interface ProtectedAttributeFlag {
  attribute: string;
  label: string;
  offset: number;
  excerpt: string;
}

export interface RequirementList {
  job_id: string;
  requirements_confirmed_at: string | null;
  requirements: Requirement[];
}

export interface ExtractionResult extends RequirementList {
  llm_call_id: string;
  source: string;
  attempts: number;
}

export function listRequirements(jobId: string, signal?: AbortSignal): Promise<RequirementList> {
  return request<RequirementList>(`/api/jobs/${jobId}/requirements`, { signal });
}

export function extractRequirements(jobId: string): Promise<ExtractionResult> {
  return jsonRequest<ExtractionResult>(`/api/jobs/${jobId}/requirements/extract`, "POST");
}

export function addRequirement(
  jobId: string,
  body: { text: string; category: RequirementCategory; must_have: boolean; weight?: string },
): Promise<Requirement> {
  return jsonRequest<Requirement>(`/api/jobs/${jobId}/requirements`, "POST", body);
}

export function updateRequirement(
  requirementId: string,
  body: Partial<{
    text: string;
    category: RequirementCategory;
    must_have: boolean;
    weight: string;
  }>,
): Promise<Requirement> {
  return jsonRequest<Requirement>(`/api/requirements/${requirementId}`, "PATCH", body);
}

export function deleteRequirement(requirementId: string): Promise<void> {
  return request<void>(`/api/requirements/${requirementId}`, { method: "DELETE" });
}

export function confirmRequirements(jobId: string): Promise<RequirementList> {
  return jsonRequest<RequirementList>(`/api/jobs/${jobId}/requirements/confirm`, "POST");
}

export function unconfirmRequirements(jobId: string): Promise<RequirementList> {
  return jsonRequest<RequirementList>(`/api/jobs/${jobId}/requirements/confirm`, "DELETE");
}

// --------------------------------------------------------------------------
// The screening vocabulary (ADR-0012)
// --------------------------------------------------------------------------

/**
 * One criterion type and the fields it collects.
 *
 * The form is built from this rather than hard-coded, so the six types the
 * interface offers are exactly the six the engine can evaluate. `subject_source`
 * names the list in the same response the subject must be chosen from.
 */
export interface CriterionType {
  spec_type: RequirementSpecType;
  label: string;
  subject_source: "degrees" | "skills" | "languages" | "certifications" | null;
  threshold_unit: "months" | "grade" | null;
  threshold_required: boolean;
  needs_scale: boolean;
}

export interface SkillOption {
  name: string;
  family: string;
}

export interface SkillGroup {
  name: string;
  skills: string[];
}

export interface DegreeOption {
  name: string;
  /** Equal ranks are the same level under two naming conventions: S1 = Bachelor. */
  rank: number;
}

export interface CriteriaVocabulary {
  spec_types: CriterionType[];
  skills: SkillOption[];
  /** Presence only. This screener never infers a proficiency level. */
  languages: string[];
  /** Credentials, by presence only: no dates, no levels, no equivalences. */
  certifications: string[];
  degrees: DegreeOption[];
  skill_groups: SkillGroup[];
}

/**
 * Everything the criteria builder is allowed to offer.
 *
 * Static for a given backend build — it depends on no job and no candidate — so
 * it is fetched once per screen rather than per keystroke.
 */
export function getCriteriaVocabulary(signal?: AbortSignal): Promise<CriteriaVocabulary> {
  return request<CriteriaVocabulary>("/api/criteria/vocabulary", { signal });
}

export interface CriterionInput {
  spec_type: RequirementSpecType;
  must_have: boolean;
  subject?: string;
  threshold_value?: string;
  threshold_scale?: string;
  weight?: string;
}

/**
 * Add one structured criterion.
 *
 * A 409 here means the subject is outside the supported vocabulary — the server
 * refuses it at creation rather than accepting a criterion it could only ever
 * answer with "needs review".
 */
export function addCriterion(jobId: string, body: CriterionInput): Promise<Requirement> {
  return jsonRequest<Requirement>(`/api/jobs/${jobId}/criteria`, "POST", body);
}

// --------------------------------------------------------------------------
// Candidates
// --------------------------------------------------------------------------

export interface UploadOutcome {
  filename: string;
  accepted: boolean;
  candidate_id: string | null;
  status: CandidateStatus | null;
  failure_reason: string | null;
  rejection_code: string | null;
  detail: string | null;
}

export interface UploadBatch {
  job_id: string;
  uploaded: number;
  rejected: number;
  results: UploadOutcome[];
}

export interface Candidate {
  id: string;
  job_id: string;
  display_name: string | null;
  status: CandidateStatus;
  failure_reason: string | null;
  failure_detail: string | null;
  created_at: string;
  document: { original_filename: string; size_bytes: number; page_count: number | null } | null;
  parsed: {
    page_count: number;
    char_count: number;
    injection_flag_count: number;
    /** Pages laid out in columns, where the reading order may be wrong. */
    multi_column_pages: number[];
  } | null;
}

export function uploadCandidates(jobId: string, files: File[]): Promise<UploadBatch> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return request<UploadBatch>(`/api/jobs/${jobId}/candidates`, { method: "POST", body: form });
}

export function listCandidates(
  jobId: string,
  signal?: AbortSignal,
): Promise<{ job_id: string; candidates: Candidate[] }> {
  return request(`/api/jobs/${jobId}/candidates`, { signal });
}

export function getCandidate(candidateId: string, signal?: AbortSignal): Promise<Candidate> {
  return request<Candidate>(`/api/candidates/${candidateId}`, { signal });
}

// --------------------------------------------------------------------------
// Profile, matching, scoring
// --------------------------------------------------------------------------

export interface Evidence {
  id: string;
  quoted_text: string;
  start_char: number | null;
  end_char: number | null;
  page_number: number | null;
  verification_status: EvidenceVerification;
  normalization_version: string;
}

export interface CandidateProfile {
  candidate_id: string;
  profile_id: string;
  prompt_version: string;
  created_at: string;
  skills: { id: string; raw_name: string; evidence: Evidence | null }[];
  experience: {
    id: string;
    role_title: string;
    organization: string | null;
    start_date: string | null;
    end_date: string | null;
    date_precision: string;
    is_current: boolean;
    description: string | null;
    evidence: Evidence | null;
  }[];
  education: {
    id: string;
    degree: string | null;
    field_of_study: string | null;
    institution: string | null;
    completion_year: number | null;
    evidence: Evidence | null;
  }[];
  projects: {
    id: string;
    name: string;
    description: string | null;
    technologies: string[];
    evidence: Evidence | null;
  }[];
  evidence_summary: { items: number; with_evidence: number; verified: number; unverified: number };
}

export function extractProfile(candidateId: string): Promise<{ profile: CandidateProfile }> {
  return jsonRequest(`/api/candidates/${candidateId}/profile`, "POST");
}

export function getProfile(candidateId: string, signal?: AbortSignal): Promise<CandidateProfile> {
  return request<CandidateProfile>(`/api/candidates/${candidateId}/profile`, { signal });
}

export interface MatchResult {
  requirement_id: string;
  requirement_text: string;
  category: RequirementCategory;
  must_have: boolean;
  display_order: number;
  verdict: MatchVerdict;
  decided_by: MatchMethod;
  reason: string;
  evidence: Evidence | null;
  raw_verdict: MatchVerdict | null;
  downgraded: boolean;
}

export interface MatchResults {
  candidate_id: string;
  job_id: string;
  requirements_confirmed_at: string | null;
  results: MatchResult[];
  summary: {
    total: number;
    matched: number;
    partial: number;
    no_evidence: number;
    /** Criteria the engine could not resolve — not the same as no evidence. */
    needs_review: number;
    downgraded: number;
    decided_deterministically: number;
    decided_by_model: number;
  };
}

export function runMatching(candidateId: string): Promise<MatchResults> {
  return jsonRequest<MatchResults>(`/api/candidates/${candidateId}/matches`, "POST");
}

export function getMatches(candidateId: string, signal?: AbortSignal): Promise<MatchResults> {
  return request<MatchResults>(`/api/candidates/${candidateId}/matches`, { signal });
}

export interface Contribution {
  requirement_id: string;
  requirement_text: string;
  category: RequirementCategory;
  must_have: boolean;
  display_order: number;
  weight: string;
  verdict: MatchVerdict;
  /**
   * Null for NEEDS_REVIEW. Not zero: an unresolved criterion takes no part in
   * the arithmetic at all, and its weight is not redistributed either.
   */
  verdict_value: string | null;
  points: string | null;
}

export interface Score {
  candidate_id: string;
  job_id: string;
  status: ScoreStatus;
  score: number | null;
  score_raw: string | null;
  weighted_sum: string | null;
  total_weight: string | null;
  must_have_coverage: string | null;
  band: RecommendationBand | null;
  band_raw: RecommendationBand | null;
  capped: boolean;
  capped_by_requirement_id: string | null;
  capped_by_requirement_text: string | null;
  /** True when at least one criterion was left unresolved. */
  review_flag: boolean;
  needs_review_count: number;
  must_have_needs_review_count: number;
  scoring_config_version: string;
  computed_at: string;
  contributions: Contribution[];
}

export function runScoring(candidateId: string): Promise<Score> {
  return jsonRequest<Score>(`/api/candidates/${candidateId}/score`, "POST");
}

export function getScore(candidateId: string, signal?: AbortSignal): Promise<Score> {
  return request<Score>(`/api/candidates/${candidateId}/score`, { signal });
}

// --------------------------------------------------------------------------
// Ranking
// --------------------------------------------------------------------------

export interface RankedCandidate {
  position: number;
  candidate_id: string;
  display_name: string | null;
  original_filename: string | null;
  status: CandidateStatus;
  score_status: ScoreStatus;
  score: number | null;
  must_have_coverage: string | null;
  band: RecommendationBand | null;
  band_raw: RecommendationBand | null;
  capped: boolean;
  matched_count: number;
  /** How much of the criteria list the score does not cover. */
  needs_review_count: number;
  warnings: string[];
  scoring_config_version: string;
  computed_at: string;
}

export interface UnscoredCandidate {
  candidate_id: string;
  display_name: string | null;
  original_filename: string | null;
  status: CandidateStatus;
}

export interface FailedCandidate extends UnscoredCandidate {
  failure_reason: string | null;
  failure_detail: string | null;
}

export interface JobRanking {
  job_id: string;
  job_title: string;
  requirements_confirmed_at: string | null;
  ranked: RankedCandidate[];
  not_yet_scored: UnscoredCandidate[];
  failed: FailedCandidate[];
  summary: { total: number; ranked: number; not_yet_scored: number; failed: number };
}

export function getRanking(jobId: string, signal?: AbortSignal): Promise<JobRanking> {
  return request<JobRanking>(`/api/jobs/${jobId}/ranking`, { signal });
}

// --------------------------------------------------------------------------
// Demo
// --------------------------------------------------------------------------

export interface SampleCriteria {
  id: string;
  label: string;
  language: string;
  demonstrates: string;
  text: string;
  full_walkthrough: boolean;
}

/** One criterion the structured demo screens with. */
export interface StructuredSampleCriterion {
  spec_type: RequirementSpecType;
  text: string;
  must_have: boolean;
  demonstrates: string;
}

export interface DemoSamples {
  demo_mode: boolean;
  job_title: string;
  job_description: string;
  /** Pass this as the criteria id to seed the structured demo. */
  structured_criteria_id: string;
  /** The six criteria that demo screens with. It needs no recordings at all. */
  structured_criteria: StructuredSampleCriterion[];
  /** The free-text briefs, which drive the legacy model-read path. */
  criteria: SampleCriteria[];
  cvs: { filename: string; label: string; demonstrates: string }[];
}

export interface DemoSeed {
  job_id: string;
  job_title: string;
  uploaded: number;
  rejected: number;
  screened: number;
  failed: number;
}

export function getDemoSamples(signal?: AbortSignal): Promise<DemoSamples> {
  return request<DemoSamples>("/api/demo/samples", { signal });
}

export function seedDemoJob(criteriaId?: string): Promise<DemoSeed> {
  const query = criteriaId ? `?criteria_id=${encodeURIComponent(criteriaId)}` : "";
  return jsonRequest<DemoSeed>(`/api/demo/jobs${query}`, "POST");
}
