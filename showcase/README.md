# CvScreener public showcase

A static project page for CvScreener: plain HTML and CSS, no build step, no
JavaScript, no dependencies. It **describes** the application; it does not run
it. The full application — React frontend, FastAPI backend, PostgreSQL and a
local language model — runs on your own machine (see
[the development guide](../docs/development.md), or double-click
`Start CvScreener.cmd` on Windows).

## What is in this folder

| File           | Purpose                                                   |
| -------------- | --------------------------------------------------------- |
| `index.html`   | The whole page                                            |
| `styles.css`   | The application's own palette and type, light and dark    |
| `favicon.svg`  | Tab icon                                                  |
| `assets/*.png` | Four screenshots of the local application's built-in demo |

The page has no backend, API calls, forms, analytics or cookies, and requests
nothing from any other site. A `Content-Security-Policy` meta tag holds it to
that (`default-src 'none'; style-src 'self'; img-src 'self'`), so an accidental
external script, font or image would be blocked rather than quietly loaded.

## Preview locally

```powershell
backend\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory showcase
```

Then open http://127.0.0.1:8765. Any static file server works the same way.

## Deploy to Vercel

The settings below follow Vercel's own documentation for a static site with no
build step ("Configuring a Build") and its Hobby plan page, as read in
September 2026.

1. Push the repository to GitHub first — see
   [Before sharing the link](#before-sharing-the-link).
2. Create a Vercel project from the GitHub repository.
3. **Root Directory:** `showcase`
4. **Framework Preset:** Other
5. **Build Command:** turn on _Override_ and leave the field empty. The build
   step is skipped and the files are served as they are.
6. **Output Directory:** leave the default. With the "Other" preset Vercel
   serves a `public` directory if one exists and otherwise the root directory,
   which here is `showcase/` itself.
7. **Environment variables:** none. There is no `package.json` in this folder,
   so there is nothing to install either.

No `vercel.json` is needed. From the command line, `vercel login` followed by
`vercel` in the repository (with the Root Directory set as above) deploys the
same way.

**Cost.** The Hobby plan is free and restricted to personal, non-commercial
use. A static page of this size (about 330 KB with its screenshots) needs no
paid service and no server-side compute.

**Other hosts.** Any static host that can serve a folder will do; nothing in
the page is specific to Vercel.

## Before sharing the link

- **Push first.** The page links to documents on the repository's `main`
  branch. Some of them exist only in local commits until those are pushed —
  for example `docs/decisions/0012-structured-screening-criteria.md` — and would
  otherwise show GitHub's 404 page.
- **Check the numbers** against their sources below.

## Where every number on the page comes from

| On the page                                                                                        | Source                                                                                                            |
| -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1,145 backend and 135 frontend tests; 97% backend coverage                                         | `.\tasks.ps1 test` and `.\tasks.ps1 coverage`, run locally                                                        |
| Structured evaluation: 97/101, 0/94, 2/94, 52/52, 45/45, 52/52, 101/101                            | [`evaluation/RESULTS.md`](../evaluation/RESULTS.md), from `.\tasks.ps1 evaluate`                                  |
| Deterministic engine against Qwen2.5-7B: 88.8% vs 82.0%, 0 vs 2 over-credits, 5.5 ms vs ~14,900 ms | [ADR-0012](../docs/decisions/0012-structured-screening-criteria.md)                                               |
| Demo scores 83 (Good Match) and 38 (Low Match)                                                     | Pinned by `backend/tests/test_demo.py`; visible in the screenshots                                                |
| Criterion types; 85 skills, 10 languages, 24 certifications                                        | [README — The screening criteria](../README.md#the-screening-criteria)                                            |
| Fairness statements and limitations                                                                | [README — Fairness](../README.md#fairness-and-its-limits) and [Known limitations](../README.md#known-limitations) |
| The profile-extraction call made when screening                                                    | [Roadmap — open follow-up](../docs/roadmap.md#after-the-roadmap-structured-screening-adr-0012)                    |

When one of these changes, update the page in the same commit.

## How the screenshots were made

They show the real application running its built-in structured demo, in which
every CV is synthetic and every name, employer and university is fictional.
They were captured from a **disposable environment**, so no real candidate data
could appear in them and nothing in a working installation was touched:

1. A temporary PostgreSQL container with no volume (`docker run --rm`, data on
   `tmpfs`), on its own port.
2. The backend on another port in demo mode, configured only through
   environment variables — `DATABASE_URL`, `DEMO_MODE=true`,
   `CORS_ALLOWED_ORIGINS`, and `UPLOAD_STORAGE_DIR` pointing outside the
   repository — then `alembic upgrade head` against that database.
3. The frontend on another port, with `VITE_API_BASE_URL` pointing at that
   backend.
4. `POST /api/demo/jobs` to seed the demo job, then headless Microsoft Edge with
   a throwaway profile to take the screenshots.
5. Everything stopped afterwards; the temporary container removed itself.

**Never capture screenshots from a database that holds real CVs.**

## Accessibility

- Landmarks, a single `h1` with no skipped heading levels, a skip link, and a
  visible focus ring on every interactive element.
- Descriptive alt text for each screenshot, plus a link to the full-size image.
- Tables have captions and header scopes. The three text-heavy tables become one
  card per row on narrow screens, with explicit ARIA table roles so the change
  of layout does not cost them their table semantics.
- Light and dark themes follow the system setting; smooth scrolling applies only
  when reduced motion is not requested, and there is no other animation.
