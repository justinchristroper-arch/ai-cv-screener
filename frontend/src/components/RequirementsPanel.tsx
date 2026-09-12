/**
 * Step 2: review the extracted requirements, then confirm them.
 *
 * This is the human confirmation gate (ADR-0004), and the screen is built
 * around the fact that it is a real decision rather than a formality. What the
 * model proposed is shown; what confirmation freezes is stated; and the two
 * fields that stay editable afterwards — weight and must-have — are the two the
 * recruiter is most likely to want to change, because changing them is free.
 */

import { useState } from "react";

import {
  addRequirement,
  confirmRequirements,
  deleteRequirement,
  extractRequirements,
  listRequirements,
  unconfirmRequirements,
  updateRequirement,
  type Requirement,
  type RequirementCategory,
} from "../api/client";
import { CATEGORY_LABEL, trimDecimal } from "../display";
import { useAction, useResource } from "../hooks/useResource";
import { Callout, EmptyState, ErrorState, Pill, Spinner } from "./ui";

const CATEGORIES: RequirementCategory[] = [
  "EDUCATION",
  "TECHNICAL_SKILL",
  "EXPERIENCE",
  "PROJECT",
  "SOFT_SKILL_OTHER",
];

export function RequirementsPanel({
  jobId,
  hasDescription,
  onChanged,
}: {
  jobId: string;
  hasDescription: boolean;
  onChanged: () => void;
}) {
  const { resource, reload } = useResource((signal) => listRequirements(jobId, signal), [jobId]);
  const extract = useAction();
  const gate = useAction();
  const edit = useAction();

  // Two different kinds of change, kept apart on purpose.
  //
  // `refreshRequirements` is for an edit that only touches a row — a weight or
  // a must-have flag. Neither is displayed in the job header, so refetching the
  // job there would be a request for data that cannot have changed.
  //
  // `refreshJob` is for a change to the requirement *set* — extraction,
  // confirmation, an addition or a deletion — which does change what the header
  // shows, and is worth the extra request.
  const refreshRequirements = () => reload();

  const refreshJob = () => {
    reload();
    onChanged();
  };

  const onExtract = async () => {
    if (await extract.run(() => extractRequirements(jobId))) refreshJob();
  };

  const onConfirm = async () => {
    if (await gate.run(() => confirmRequirements(jobId))) refreshJob();
  };

  const onUnconfirm = async () => {
    if (await gate.run(() => unconfirmRequirements(jobId))) refreshJob();
  };

  if (resource.state === "loading") return <Spinner label="Loading requirements…" />;
  if (resource.state === "error") return <ErrorState error={resource.error} onRetry={reload} />;

  const { requirements, requirements_confirmed_at: confirmedAt } = resource.data;
  const confirmed = confirmedAt !== null;
  const edited = requirements.filter(
    (item) =>
      item.proposed_text !== null &&
      (item.proposed_text !== item.text ||
        item.proposed_category !== item.category ||
        item.proposed_must_have !== item.must_have),
  ).length;
  // Requirements the confirmation gate will refuse. Surfaced here so the wall
  // is visible while it can still be walked around, not only when it is hit.
  const blocking = requirements.filter((item) => item.protected_attribute_flags.length > 0);

  return (
    <section className="panel" aria-labelledby="req-heading">
      <div className="panel__header">
        <h2 id="req-heading">2 · Requirements</h2>
        {!confirmed && hasDescription ? (
          <button
            type="button"
            className="button button--quiet"
            onClick={onExtract}
            disabled={extract.busy}
          >
            {extract.busy
              ? "Extracting…"
              : requirements.length
                ? "Re-extract"
                : "Extract from description"}
          </button>
        ) : null}
      </div>

      {!hasDescription ? (
        <EmptyState title="Add a job description first">
          Requirements are extracted from the description, so there is nothing to extract yet.
        </EmptyState>
      ) : null}

      {extract.error ? <ErrorState error={extract.error} /> : null}

      {hasDescription && requirements.length === 0 && !extract.busy ? (
        <EmptyState title="No requirements yet">
          Extract them from the description, then review every line before confirming. The model
          proposes; you decide.
        </EmptyState>
      ) : null}

      {requirements.length > 0 ? (
        <>
          <p className="panel__hint">
            {requirements.length} requirement{requirements.length === 1 ? "" : "s"}
            {edited > 0 ? ` · ${edited} edited from what the model proposed` : null}
          </p>

          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Requirement</th>
                  <th scope="col">Category</th>
                  <th scope="col">Must have</th>
                  <th scope="col">Weight</th>
                  {!confirmed ? (
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {requirements.map((item) => (
                  <RequirementRow
                    key={item.id}
                    requirement={item}
                    confirmed={confirmed}
                    // Only the row being saved is disabled. Disabling the whole
                    // panel would blur whatever control the person was using.
                    busy={edit.pending === item.id}
                    onUpdate={async (body) => {
                      if (await edit.run(() => updateRequirement(item.id, body), item.id)) {
                        refreshRequirements();
                      }
                    }}
                    onDelete={async () => {
                      if (await edit.run(() => deleteRequirement(item.id), item.id)) refreshJob();
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
          {edit.error ? <ErrorState error={edit.error} /> : null}

          {!confirmed ? <AddRequirement jobId={jobId} onAdded={refreshJob} /> : null}

          {blocking.length > 0 && !confirmed ? (
            <Callout tone="warn" title="These requirements cannot be confirmed">
              <p>
                {blocking.length} requirement{blocking.length === 1 ? "" : "s"} below
                {blocking.length === 1 ? " asks" : " ask"} about a personal characteristic rather
                than about someone&rsquo;s work. Remove or reword{" "}
                {blocking.length === 1 ? "it" : "them"}, then confirm.
              </p>
              <p>
                This tool holds no such information about anybody &mdash; age, gender, marital
                status, religion, ethnicity, nationality, appearance and health are never extracted
                from a CV &mdash; so a requirement about one could never be answered from evidence,
                and no candidate will ever be screened on it.
              </p>
            </Callout>
          ) : null}

          <Callout
            tone={confirmed ? "info" : "warn"}
            title={
              confirmed ? "Requirements confirmed" : "Confirmation is required before screening"
            }
          >
            {confirmed ? (
              <>
                Wording and category are frozen. <strong>Weight and must-have stay editable</strong>{" "}
                — they change the arithmetic, not the verdicts, so re-weighting costs nothing and
                needs no model call. Unconfirming discards every verdict and score in this job.
              </>
            ) : (
              <>
                No candidate can be matched or scored until a human confirms this set. Review the
                wording, the categories and the must-have flags first — an error here would quietly
                affect every candidate.
              </>
            )}
          </Callout>

          <div className="button-row">
            {confirmed ? (
              <button
                type="button"
                className="button button--quiet"
                onClick={onUnconfirm}
                disabled={gate.busy}
              >
                {gate.busy ? "Working…" : "Unconfirm and edit"}
              </button>
            ) : (
              <button type="button" className="button" onClick={onConfirm} disabled={gate.busy}>
                {gate.busy ? "Confirming…" : "Confirm requirements"}
              </button>
            )}
          </div>
          {gate.error ? <ErrorState error={gate.error} /> : null}
        </>
      ) : null}
    </section>
  );
}

function RequirementRow({
  requirement,
  confirmed,
  busy,
  onUpdate,
  onDelete,
}: {
  requirement: Requirement;
  confirmed: boolean;
  busy: boolean;
  onUpdate: (body: Partial<{ must_have: boolean; weight: string }>) => void;
  onDelete: () => void;
}) {
  const wasEdited =
    requirement.proposed_text !== null &&
    (requirement.proposed_text !== requirement.text ||
      requirement.proposed_category !== requirement.category ||
      requirement.proposed_must_have !== requirement.must_have);

  return (
    <tr>
      <td>
        <span className="req-text">{requirement.text}</span>
        <span className="req-tags">
          {requirement.origin === "HR_ADDED" ? <Pill tone="added">Added by you</Pill> : null}
          {wasEdited ? <Pill tone="edited">Edited</Pill> : null}
          {requirement.protected_attribute_flags.map((flag) => (
            <Pill key={flag.attribute} tone="blocked">
              {flag.label}
            </Pill>
          ))}
        </span>
        {requirement.protected_attribute_flags.length > 0 ? (
          <span className="req-blocked">
            Cannot be screened on. Remove or reword this requirement to confirm the set.
          </span>
        ) : null}
      </td>
      <td>{CATEGORY_LABEL[requirement.category]}</td>
      <td>
        {/* aria-label rather than the wrapping label alone. A visually-hidden
            span inside a <label> names the input in jsdom, and the test suite
            was happy with it — but reading the real accessibility tree in a
            browser showed the checkbox announced as "on". The weight input next
            to it already used aria-label; this one now matches. The span stays
            so the whole cell remains a click target. */}
        <label className="switch">
          <input
            type="checkbox"
            checked={requirement.must_have}
            disabled={busy}
            aria-label={`Must have: ${requirement.text}`}
            onChange={(event) => onUpdate({ must_have: event.target.checked })}
          />
          <span className="visually-hidden">Must have: {requirement.text}</span>
        </label>
      </td>
      <td>
        {/* Uncontrolled, so a half-typed number is not fought over on every
            keystroke — but keyed on the stored weight so that when the server
            settles on a different value (it stores two decimal places) the box
            shows what was actually saved rather than what was typed. */}
        <input
          key={requirement.weight}
          type="number"
          className="weight-input"
          min={0}
          max={100}
          step={0.5}
          disabled={busy}
          defaultValue={trimDecimal(requirement.weight)}
          aria-label={`Weight for: ${requirement.text}`}
          onBlur={(event) => {
            const next = event.target.value.trim();
            if (next !== "" && next !== trimDecimal(requirement.weight)) onUpdate({ weight: next });
          }}
        />
      </td>
      {!confirmed ? (
        <td>
          <button
            type="button"
            className="button button--danger"
            onClick={onDelete}
            disabled={busy}
          >
            Remove
          </button>
        </td>
      ) : null}
    </tr>
  );
}

/**
 * The free-text tools, on their own, for embedding elsewhere.
 *
 * Structured criteria are the main path now (ADR-0012), but a job description
 * is still the input a recruiter most often has to hand, and the model reading
 * it is still useful. Rather than delete a working flow, `CriteriaPanel` offers
 * these two behind a disclosure: extract requirements from the description, and
 * add a free-text line the six types cannot express.
 *
 * What such a requirement costs is stated where it is offered: it is judged by
 * the model rather than by the deterministic rules, so it is slower, needs the
 * model to be reachable, and its verdict is not reconstructible from the CV by
 * arithmetic alone.
 */
export function LegacyRequirementTools({
  jobId,
  hasDescription,
  hasRequirements,
  onChanged,
}: {
  jobId: string;
  hasDescription: boolean;
  hasRequirements: boolean;
  onChanged: () => void;
}) {
  const extract = useAction();

  const onExtract = async () => {
    if (await extract.run(() => extractRequirements(jobId))) onChanged();
  };

  return (
    <div className="legacy-tools">
      {hasDescription ? (
        <button
          type="button"
          className="button button--quiet"
          onClick={onExtract}
          disabled={extract.busy}
        >
          {extract.busy
            ? "Extracting…"
            : hasRequirements
              ? "Re-extract from the description"
              : "Extract requirements from the description"}
        </button>
      ) : (
        <p className="panel__hint">
          Attach a job description in step 1 to extract requirements from it.
        </p>
      )}
      {extract.error ? <ErrorState error={extract.error} /> : null}
      <AddRequirement jobId={jobId} onAdded={onChanged} />
    </div>
  );
}

function AddRequirement({ jobId, onAdded }: { jobId: string; onAdded: () => void }) {
  const add = useAction();
  const [text, setText] = useState("");
  const [category, setCategory] = useState<RequirementCategory>("TECHNICAL_SKILL");
  const [mustHave, setMustHave] = useState(false);

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    const ok = await add.run(() =>
      addRequirement(jobId, { text: trimmed, category, must_have: mustHave }),
    );
    if (ok) {
      setText("");
      onAdded();
    }
  };

  return (
    <form className="add-requirement" onSubmit={onSubmit}>
      <label className="field field--grow">
        <span className="field__label">Add a requirement the description missed</span>
        <input
          type="text"
          value={text}
          maxLength={300}
          placeholder="e.g. Experience mentoring junior engineers"
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <label className="field">
        <span className="field__label">Category</span>
        <select
          value={category}
          onChange={(event) => setCategory(event.target.value as RequirementCategory)}
        >
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {CATEGORY_LABEL[value]}
            </option>
          ))}
        </select>
      </label>
      <label className="field field--checkbox">
        <input
          type="checkbox"
          checked={mustHave}
          onChange={(event) => setMustHave(event.target.checked)}
        />
        <span className="field__label">Must have</span>
      </label>
      <button type="submit" className="button button--quiet" disabled={add.busy || !text.trim()}>
        {add.busy ? "Adding…" : "Add"}
      </button>
      {add.error ? <ErrorState error={add.error} /> : null}
    </form>
  );
}
