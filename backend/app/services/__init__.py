"""Business logic — one module per pipeline stage.

Implemented so far (docs/architecture.md section 3):

* ``jobs`` — stage 1, jobs and their job descriptions.
* ``jd_extraction`` — stage 2, the LLM requirement extraction.
* ``requirements`` — stages 3 and 4, HR review and the confirmation gate.

Stages 5-12 (parsing, profile extraction, evidence, matching, semantic
evaluation, scoring, ranking) arrive in Phases 5-10.

Every business rule lives here rather than in ``api/routes`` so that no route,
background task, or future caller can reach the database around it. The
confirmation gate in ``requirements`` is the rule that most depends on this.
"""
