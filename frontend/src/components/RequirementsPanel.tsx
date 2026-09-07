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

  const refresh = () => {
    reload();
    onChanged();
  };

  const onExtract = async () => {
    if (await extract.run(() => extractRequirements(jobId))) refresh();
  };

  const onConfirm = async () => {
    if (await gate.run(() => confirmRequirements(jobId))) refresh();
  };

  const onUnconfirm = async () => {
    if (await gate.run(() => unconfirmRequirements(jobId))) refresh();
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
                    busy={edit.busy}
                    onUpdate={async (body) => {
                      if (await edit.run(() => updateRequirement(item.id, body))) refresh();
                    }}
                    onDelete={async () => {
                      if (await edit.run(() => deleteRequirement(item.id))) refresh();
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
          {edit.error ? <ErrorState error={edit.error} /> : null}

          {!confirmed ? <AddRequirement jobId={jobId} onAdded={refresh} /> : null}

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
        </span>
      </td>
      <td>{CATEGORY_LABEL[requirement.category]}</td>
      <td>
        <label className="switch">
          <input
            type="checkbox"
            checked={requirement.must_have}
            disabled={busy}
            onChange={(event) => onUpdate({ must_have: event.target.checked })}
          />
          <span className="visually-hidden">Must have: {requirement.text}</span>
        </label>
      </td>
      <td>
        <input
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
