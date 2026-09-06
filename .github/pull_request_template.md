## What changed and why

<!-- Not just what — why. Link the roadmap phase (docs/roadmap.md) or ADR
(docs/decisions/) this relates to, if any. -->

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass (backend), or n/a
- [ ] `eslint . --max-warnings 0` and `prettier --check .` pass (frontend), or n/a
- [ ] `pytest` passes, including any new `requires_db` tests run against a real database at least once
- [ ] `npm test` passes (frontend), or n/a
- [ ] Documentation updated if this changes an API shape, a required environment variable, or a decision recorded in `docs/decisions/`
- [ ] No `.env`, credential, real candidate data, or generated artifact is included in this diff

## How this was verified

<!-- What you actually ran, and what it actually printed — not "should work." -->
