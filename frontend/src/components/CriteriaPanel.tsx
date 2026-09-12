/**
 * Step 2: choose what to screen for, then confirm it.
 *
 * The recruiter picks from a closed set of structured criteria rather than typing a
 * sentence (ADR-0012). That is a narrowing, and the screen is honest about it:
 * the menu says those are everything the engine can evaluate, the skill
 * search says so again for a term that is not in the list, and the free-text
 * path is still reachable — labelled with what it costs — rather than removed.
 *
 * The reason for the narrowing is visible in what comes out the other end. A
 * structured criterion is answered by arithmetic over the CV's own text, so its
 * verdict cites a line, reconstructs exactly, and means the same thing on every
 * run. A sentence is answered by a model, and it does not.
 *
 * The confirmation gate (ADR-0004) is unchanged and still lives here: nothing
 * is screened until a human freezes this set.
 */

import { useMemo, useState } from "react";

import {
  addCriterion,
  confirmRequirements,
  deleteRequirement,
  getCriteriaVocabulary,
  listRequirements,
  unconfirmRequirements,
  updateRequirement,
  type CriteriaVocabulary,
  type CriterionInput,
  type CriterionType,
  type Requirement,
} from "../api/client";
import { SPEC_TYPE_HINT, trimDecimal } from "../display";
import { useAction, useResource } from "../hooks/useResource";
import { LegacyRequirementTools } from "./RequirementsPanel";
import { Callout, EmptyState, ErrorState, Pill, Spinner } from "./ui";

/** Turn a count of months into the way a person would say it. */
function describeMonths(raw: string): string | null {
  const months = Number.parseInt(raw, 10);
  if (!Number.isFinite(months) || months <= 0) return null;
  if (months % 12 === 0) {
    const years = months / 12;
    return `${years} year${years === 1 ? "" : "s"}`;
  }
  if (months < 12) return `${months} month${months === 1 ? "" : "s"}`;
  return `${Math.floor(months / 12)} year${months >= 24 ? "s" : ""} ${months % 12} month${
    months % 12 === 1 ? "" : "s"
  }`;
}

export function CriteriaPanel({
  jobId,
  hasDescription,
  onChanged,
}: {
  jobId: string;
  hasDescription: boolean;
  onChanged: () => void;
}) {
  const { resource, reload } = useResource((signal) => listRequirements(jobId, signal), [jobId]);
  // Static for a backend build, so it is fetched once for the screen rather
  // than on every keystroke of the skill search.
  const vocabulary = useResource((signal) => getCriteriaVocabulary(signal), []);
  const gate = useAction();
  const edit = useAction();

  const refreshCriteria = () => reload();
  const refreshJob = () => {
    reload();
    onChanged();
  };

  const onConfirm = async () => {
    if (await gate.run(() => confirmRequirements(jobId))) refreshJob();
  };

  const onUnconfirm = async () => {
    if (await gate.run(() => unconfirmRequirements(jobId))) refreshJob();
  };

  if (resource.state === "loading") return <Spinner label="Loading criteria…" />;
  if (resource.state === "error") return <ErrorState error={resource.error} onRetry={reload} />;

  const { requirements, requirements_confirmed_at: confirmedAt } = resource.data;
  const confirmed = confirmedAt !== null;
  const freeText = requirements.filter((item) => item.spec_type === null).length;
  // Criteria the confirmation gate will refuse. Surfaced here so the wall is
  // visible while it can still be walked around, not only when it is hit.
  const blocking = requirements.filter((item) => item.protected_attribute_flags.length > 0);

  return (
    <section className="panel" aria-labelledby="criteria-heading">
      <div className="panel__header">
        <h2 id="criteria-heading">2 · Screening criteria</h2>
      </div>

      {requirements.length === 0 ? (
        <EmptyState title="No criteria yet">
          Add what this role actually requires — a degree level, a minimum GPA, a length of
          experience, a skill, an internship, or a language. Each one is answered from the
          CV&rsquo;s own words, and every candidate is measured against the same list.
        </EmptyState>
      ) : (
        <>
          <p className="panel__hint">
            {requirements.length} criteri{requirements.length === 1 ? "on" : "a"}
            {freeText > 0 ? ` · ${freeText} judged by the model` : null}
          </p>

          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Criterion</th>
                  <th scope="col">Decided by</th>
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
                  <CriterionRow
                    key={item.id}
                    criterion={item}
                    confirmed={confirmed}
                    // Only the row being saved is disabled. Disabling the whole
                    // panel would blur whatever control the person was using.
                    busy={edit.pending === item.id}
                    onUpdate={async (body) => {
                      if (await edit.run(() => updateRequirement(item.id, body), item.id)) {
                        refreshCriteria();
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
        </>
      )}

      {!confirmed ? (
        <>
          {vocabulary.resource.state === "loading" ? (
            <Spinner label="Loading the supported criteria…" />
          ) : null}
          {vocabulary.resource.state === "error" ? (
            <ErrorState error={vocabulary.resource.error} onRetry={vocabulary.reload} />
          ) : null}
          {vocabulary.resource.state === "ready" ? (
            <AddCriterion
              jobId={jobId}
              vocabulary={vocabulary.resource.data}
              onAdded={refreshJob}
            />
          ) : null}

          <details className="disclosure">
            <summary>Screen for something these criteria cannot express</summary>
            <div className="disclosure__body">
              <p>
                A criterion written as a sentence is read by the language model rather than by the
                screening rules. It is slower, it needs the model to be reachable, and its verdict
                cannot be reproduced from the CV by arithmetic — so it is kept here rather than
                offered first. Everything above still applies to it: nothing is screened until you
                confirm, and no verdict is ever recorded without a quotation from the CV.
              </p>
              <LegacyRequirementTools
                jobId={jobId}
                hasDescription={hasDescription}
                hasRequirements={requirements.length > 0}
                onChanged={refreshJob}
              />
            </div>
          </details>
        </>
      ) : null}

      {blocking.length > 0 && !confirmed ? (
        <Callout tone="warn" title="These criteria cannot be confirmed">
          <p>
            {blocking.length} criteri{blocking.length === 1 ? "on" : "a"} below
            {blocking.length === 1 ? " asks" : " ask"} about a personal characteristic rather than
            about someone&rsquo;s work. Remove or reword {blocking.length === 1 ? "it" : "them"},
            then confirm.
          </p>
          <p>
            This tool holds no such information about anybody &mdash; age, gender, marital status,
            religion, ethnicity, nationality, appearance and health are never extracted from a CV
            &mdash; so a criterion about one could never be answered from evidence, and no candidate
            will ever be screened on it.
          </p>
        </Callout>
      ) : null}

      {requirements.length > 0 ? (
        <>
          <Callout
            tone={confirmed ? "info" : "warn"}
            title={confirmed ? "Criteria confirmed" : "Confirmation is required before screening"}
          >
            {confirmed ? (
              <>
                The criteria themselves are frozen.{" "}
                <strong>Weight and must-have stay editable</strong> — they change the arithmetic,
                not the verdicts, so re-weighting costs nothing and needs no model call.
                Unconfirming discards every verdict and score in this job.
              </>
            ) : (
              <>
                No candidate can be matched or scored until a human confirms this set. Check the
                thresholds and the must-have flags first — an error here would quietly affect every
                candidate.
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
                {gate.busy ? "Confirming…" : "Confirm criteria"}
              </button>
            )}
          </div>
          {gate.error ? <ErrorState error={gate.error} /> : null}
        </>
      ) : null}
    </section>
  );
}

// --------------------------------------------------------------------------
// One row
// --------------------------------------------------------------------------

function CriterionRow({
  criterion,
  confirmed,
  busy,
  onUpdate,
  onDelete,
}: {
  criterion: Requirement;
  confirmed: boolean;
  busy: boolean;
  onUpdate: (body: Partial<{ must_have: boolean; weight: string }>) => void;
  onDelete: () => void;
}) {
  const structured = criterion.spec_type !== null;

  return (
    <tr>
      <td>
        <span className="req-text">{criterion.text}</span>
        <span className="req-tags">
          {criterion.protected_attribute_flags.map((flag) => (
            <Pill key={flag.attribute} tone="blocked">
              {flag.label}
            </Pill>
          ))}
        </span>
        {criterion.protected_attribute_flags.length > 0 ? (
          <span className="req-blocked">
            Cannot be screened on. Remove or reword this criterion to confirm the set.
          </span>
        ) : null}
      </td>
      <td>
        {/* The distinction that matters most on this screen, so it is a column
            rather than a footnote: one of these is reproducible arithmetic over
            the CV's own words, the other is a model's reading of a sentence. */}
        <Pill tone={structured ? "ok" : "neutral"}>
          {structured ? "Screening rules" : "Language model"}
        </Pill>
      </td>
      <td>
        <label className="switch">
          <input
            type="checkbox"
            checked={criterion.must_have}
            disabled={busy}
            aria-label={`Must have: ${criterion.text}`}
            onChange={(event) => onUpdate({ must_have: event.target.checked })}
          />
          <span className="visually-hidden">Must have: {criterion.text}</span>
        </label>
      </td>
      <td>
        {/* Uncontrolled, so a half-typed number is not fought over on every
            keystroke — but keyed on the stored weight so that when the server
            settles on a different value it shows what was actually saved. */}
        <input
          key={criterion.weight}
          type="number"
          className="weight-input"
          min={0}
          max={100}
          step={0.5}
          disabled={busy}
          defaultValue={trimDecimal(criterion.weight)}
          aria-label={`Weight for: ${criterion.text}`}
          onBlur={(event) => {
            const next = event.target.value.trim();
            if (next !== "" && next !== trimDecimal(criterion.weight)) onUpdate({ weight: next });
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

// --------------------------------------------------------------------------
// Adding a criterion
// --------------------------------------------------------------------------

function AddCriterion({
  jobId,
  vocabulary,
  onAdded,
}: {
  jobId: string;
  vocabulary: CriteriaVocabulary;
  onAdded: () => void;
}) {
  const add = useAction();
  const [menuOpen, setMenuOpen] = useState(false);
  const [chosen, setChosen] = useState<CriterionType | null>(null);

  const submit = async (body: CriterionInput) => {
    if (await add.run(() => addCriterion(jobId, body))) {
      setChosen(null);
      onAdded();
    }
  };

  const addGroup = async (skills: string[]) => {
    // Sequential rather than parallel: display_order is assigned from the
    // current maximum, so concurrent inserts would race for the same position.
    const ok = await add.run(async () => {
      for (const name of skills) {
        await addCriterion(jobId, { spec_type: "SKILL", subject: name, must_have: false });
      }
    });
    if (ok) onAdded();
  };

  return (
    <div className="criteria-add">
      <div className="criteria-add__bar">
        <button
          type="button"
          className="button button--quiet"
          aria-expanded={menuOpen}
          aria-haspopup="true"
          onClick={() => {
            setMenuOpen(!menuOpen);
            setChosen(null);
          }}
        >
          + Add criterion
        </button>
      </div>

      {menuOpen ? (
        <div className="criteria-menu">
          <ul className="criteria-menu__list">
            {vocabulary.spec_types.map((type) => (
              <li key={type.spec_type}>
                <button
                  type="button"
                  className="criteria-menu__item"
                  onClick={() => {
                    setChosen(type);
                    setMenuOpen(false);
                  }}
                >
                  <span className="criteria-menu__label">{type.label}</span>
                  <span className="criteria-menu__hint">{SPEC_TYPE_HINT[type.spec_type]}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="criteria-menu__note">
            These six are everything this screener can evaluate from a CV on its own. Other criteria
            aren&rsquo;t supported yet — a CV rarely states them in a form that can be read the same
            way twice, and guessing at one would put a number on something nobody checked.
          </p>
        </div>
      ) : null}

      {chosen ? (
        <CriterionForm
          key={chosen.spec_type}
          type={chosen}
          vocabulary={vocabulary}
          busy={add.busy}
          onCancel={() => setChosen(null)}
          onSubmit={submit}
        />
      ) : null}

      {!chosen && !menuOpen ? (
        <SkillGroups groups={vocabulary.skill_groups} busy={add.busy} onAdd={addGroup} />
      ) : null}

      {add.error ? <ErrorState error={add.error} /> : null}
    </div>
  );
}

function SkillGroups({
  groups,
  busy,
  onAdd,
}: {
  groups: CriteriaVocabulary["skill_groups"];
  busy: boolean;
  onAdd: (skills: string[]) => void;
}) {
  return (
    <div className="skill-groups">
      <p className="skill-groups__label">
        {/* A preset is a shortcut for adding several ordinary skill criteria,
            never a criterion of its own: "good at AI/ML" is not something a CV
            states in a way anyone could check. */}
        Or add a set of related skills at once. Each becomes its own criterion, which you can then
        weight or remove individually.
      </p>
      <div className="skill-groups__row">
        {groups.map((group) => (
          <button
            key={group.name}
            type="button"
            className="chip"
            disabled={busy}
            title={group.skills.join(", ")}
            onClick={() => onAdd(group.skills)}
          >
            + {group.name} ({group.skills.length})
          </button>
        ))}
      </div>
    </div>
  );
}

function CriterionForm({
  type,
  vocabulary,
  busy,
  onCancel,
  onSubmit,
}: {
  type: CriterionType;
  vocabulary: CriteriaVocabulary;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (body: CriterionInput) => void;
}) {
  const [subject, setSubject] = useState("");
  const [value, setValue] = useState("");
  // Only 4.0 and 5.0 are offered because those are the only scales a CV states
  // in a form this screener reads. Offering a third would let a recruiter set a
  // minimum that no CV could ever be compared against.
  const [scale, setScale] = useState("4.00");
  const [mustHave, setMustHave] = useState(false);

  const needsSubject = type.subject_source !== null;
  const ready =
    (!needsSubject || subject !== "") && (!type.threshold_required || value.trim() !== "");

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    const body: CriterionInput = { spec_type: type.spec_type, must_have: mustHave };
    if (needsSubject) body.subject = subject;
    if (type.threshold_unit !== null && value.trim() !== "") body.threshold_value = value.trim();
    if (type.needs_scale) body.threshold_scale = scale;
    onSubmit(body);
  };

  const duration = type.threshold_unit === "months" ? describeMonths(value) : null;

  return (
    // Named, so assistive technology (and a test) can tell this form apart
    // from the free-text one behind the disclosure below it.
    <form
      className="criterion-form"
      aria-label={`New criterion: ${type.label}`}
      onSubmit={handleSubmit}
    >
      <div className="criterion-form__head">
        <h3>{type.label}</h3>
        <p className="criterion-form__hint">{SPEC_TYPE_HINT[type.spec_type]}</p>
      </div>

      {type.subject_source === "skills" ? (
        <SkillPicker skills={vocabulary.skills} value={subject} onChange={setSubject} />
      ) : null}

      {type.subject_source === "degrees" ? (
        <label className="field">
          <span className="field__label">Minimum level</span>
          <select value={subject} onChange={(event) => setSubject(event.target.value)}>
            <option value="">Choose a level…</option>
            {vocabulary.degrees.map((degree) => (
              <option key={degree.name} value={degree.name}>
                {degree.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {type.subject_source === "languages" ? (
        <label className="field">
          <span className="field__label">Language</span>
          <select value={subject} onChange={(event) => setSubject(event.target.value)}>
            <option value="">Choose a language…</option>
            {vocabulary.languages.map((language) => (
              <option key={language} value={language}>
                {language}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {type.subject_source === "certifications" ? (
        <label className="field">
          <span className="field__label">Certification</span>
          <select value={subject} onChange={(event) => setSubject(event.target.value)}>
            <option value="">Choose a certification…</option>
            {vocabulary.certifications.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <span className="field__note">
            Presence only. A certificate&rsquo;s date is never compared, and one credential never
            stands in for another.
          </span>
        </label>
      ) : null}

      {type.threshold_unit === "months" ? (
        <label className="field">
          <span className="field__label">
            Minimum duration in months{type.threshold_required ? "" : " (optional)"}
          </span>
          <input
            type="number"
            min={0}
            max={1200}
            step={1}
            value={value}
            placeholder={type.threshold_required ? "24" : "leave empty for any"}
            onChange={(event) => setValue(event.target.value)}
          />
          <span className="field__note">
            {duration ? `= ${duration}` : "Overlapping roles in a CV are counted once."}
          </span>
        </label>
      ) : null}

      {type.threshold_unit === "grade" ? (
        <div className="criterion-form__pair">
          <label className="field">
            <span className="field__label">Minimum grade</span>
            <input
              type="number"
              min={0}
              max={Number(scale)}
              step={0.01}
              value={value}
              placeholder="3.00"
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field__label">Out of</span>
            <select value={scale} onChange={(event) => setScale(event.target.value)}>
              <option value="4.00">4.00</option>
              <option value="5.00">5.00</option>
            </select>
            <span className="field__note">
              A CV stating a grade on a different scale is left for review, not converted.
            </span>
          </label>
        </div>
      ) : null}

      <label className="field field--checkbox">
        <input
          type="checkbox"
          checked={mustHave}
          onChange={(event) => setMustHave(event.target.checked)}
        />
        <span className="field__label">Must have</span>
      </label>

      <div className="button-row">
        <button type="submit" className="button" disabled={busy || !ready}>
          {busy ? "Adding…" : "Add criterion"}
        </button>
        <button type="button" className="button button--quiet" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/**
 * Choosing a skill from the supported list, and saying so when it is not there.
 *
 * The refusal is the point. The prototype this replaced accepted any word and
 * reported "no evidence" for one it did not know, which is indistinguishable
 * from the candidate not having the skill — a gap in our vocabulary read as a
 * gap in a person. Here the limit is stated before anything is screened.
 */
function SkillPicker({
  skills,
  value,
  onChange,
}: {
  skills: CriteriaVocabulary["skills"];
  value: string;
  onChange: (name: string) => void;
}) {
  const [query, setQuery] = useState("");

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return skills.slice(0, 8);
    return skills.filter((skill) => skill.name.toLowerCase().includes(needle)).slice(0, 12);
  }, [query, skills]);

  if (value) {
    return (
      <div className="field">
        <span className="field__label">Skill</span>
        <p className="skill-picker__chosen">
          <strong>{value}</strong>
          <button
            type="button"
            className="button button--quiet"
            onClick={() => {
              onChange("");
              setQuery("");
            }}
          >
            Change
          </button>
        </p>
      </div>
    );
  }

  return (
    <div className="field skill-picker">
      <label className="field__label" htmlFor="skill-search">
        Skill
      </label>
      <input
        id="skill-search"
        type="search"
        value={query}
        autoComplete="off"
        placeholder="Search the supported skills…"
        onChange={(event) => setQuery(event.target.value)}
      />
      {matches.length > 0 ? (
        <ul className="skill-picker__results">
          {matches.map((skill) => (
            <li key={skill.name}>
              <button type="button" className="chip" onClick={() => onChange(skill.name)}>
                {skill.name}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="skill-picker__unsupported" role="status">
          <strong>Skill not currently supported.</strong> &ldquo;{query.trim()}&rdquo; is not one of
          the {skills.length} skills this screener can recognise in a CV, so it cannot be screened
          for. Adding it anyway would mean reporting a gap in our vocabulary as a gap in the
          candidate. Search for a related supported skill, or keep this one for the interview.
        </p>
      )}
    </div>
  );
}
