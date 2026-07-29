# swn-localization-flask

A full Python/Flask + htmx rewrite of `netlify-localization` — server-rendered
templates instead of React, one Python stack instead of split JS/Python. The
original app (`netlify-localization`, React + Netlify Functions) keeps running
completely untouched — this is a separate, independent replacement, not a
frontend pointed at a shared backend.

Same Supabase Postgres database and schema as the original. The `/api/*` JSON
routes from an earlier draft of this project are still present (ported 1:1
from the original's Netlify Functions) but unused by the real UI — the htmx
templates talk to their own routes, which return HTML fragments, not JSON.

## Status

All 5 tabs from the original app are built and verified (curl for every
individual action, plus real-browser screenshots + a live htmx interaction
test — sidebar navigation via `hx-boost`, live filtering — with zero console
errors):

- **Translations / Completed** — page sidebar (sort by name/missing, search,
  status filter), per-key edit/approve/skip/delete/draft-with-AI/history,
  per-page draft-all-with-AI, mark complete/reopen, publish toggle, CSV
  export/import, twig export download.
- **Glossary** — inline add/edit/delete, each field autosaves on blur.
- **Style guide** — autosave on type (debounced), same as translation text.
- **Import** — paste raw .twig/HTML, extract (reuses the sentinel-leak-fixed
  extraction logic), review/edit the key table, add/delete rows, commit
  (idempotent upsert, same as the original's `import_page.py`).
- **AI drafting has three pluggable backends** (`app/drafting.py`, selected
  via `DRAFT_BACKEND`): Gemini (default — the only one to ever use on
  anything deployed), Ollama (local-only, no daily request quota), and
  OpenRouter (cloud-hosted, free tier, works anywhere including deployed).
  All three share the exact same prompt-building/batching/glossary/
  style-guide logic and structured-JSON-output contract — only the "call
  the model" step branches.
  - **Ollama** uses `gemma4:e2b` (a general instruction-following model,
    not Gemma's translation-specialized `translategemma` sibling — that
    one's fixed single-string prompt format can't carry this app's
    glossary/style-guide/batch context). Deliberately the smaller "edge"
    size, not `gemma4:12b` — measured directly: 12b took 7+ minutes on a
    glossary-constrained prompt and never finished naturally (had to be
    killed); e2b did the identical prompt correctly in ~33s. Requires
    `brew install ollama`, `brew services start ollama`, and
    `ollama pull gemma4:e2b` (~7.2GB). Only one Ollama install should ever
    run at a time — this machine had both the Homebrew CLI version and the
    `/Applications/Ollama.app` GUI app running simultaneously at one
    point, which caused exactly the kind of stuck/hung generation
    described above; Homebrew's was removed, keeping the GUI app as the
    one to use.
  - **OpenRouter** uses the free-tier `google/gemma-4-31b-it:free` (full
    31B dense model, cloud-hosted — not bottlenecked by local hardware the
    way `gemma4:12b` was). OpenAI-compatible API, so structured output uses
    `response_format: {"type": "json_schema", ...}` instead of Gemini's
    `response_schema`/Ollama's `format=` — same JSON-array-matched-by-
    keyId contract underneath, just wrapped in an object at the schema
    root (OpenAI-style structured outputs require an object root; the
    array gets unwrapped right after the call). Needs `OPENROUTER_API_KEY`
    (free to create at openrouter.ai). The free tier has its own
    per-minute/per-day rate limit that isn't published in a way that's
    checkable outside the OpenRouter dashboard — if bulk drafting starts
    failing with 429s, that's why; each batch failure surfaces the real
    error message in the toast either way.

**Not done yet, deliberately deferred:**
- **Auth + roles.** `app/auth.py` is still a mock-user stub (`AUTH_MODE=mock`).
  Full design decided, not yet built — see below.
- **Deployment.** Nothing here is deployed anywhere yet. Candidates
  discussed: Render (free tier, but cold-starts after inactivity) or
  Railway (no cold start, but no real long-term free tier). Decided this
  isn't urgent — the original `netlify-localization` app is already live
  and already covers the actual near-term need (Birthe reviewing/approving
  translations online), so this project stays local-only until the auth
  work below is done and there's a real reason to put it somewhere.

### Auth + roles plan (decided, not yet implemented)

Users: you (admin) + Birthe (editor) + up to ~2 more reviewers (approver).
Small, fixed, known set — no self-service signup/password-reset needed, so
deliberately NOT using Flask-Security-Too (its whole value is self-service
user lifecycle at scale, and it requires an ORM — this app has none, using
raw `psycopg2` everywhere; adopting it means a second data-access pattern
for a feature this app's actual scale doesn't need). Also deliberately NOT
using the full `supabase` pip package (pulls in Realtime/Storage/PostgREST/
Functions clients this app will never touch) or keeping a live Supabase
session going per-request (no RLS/PostgREST usage here to justify it).

**Architecture:**
- Supabase Auth's role is narrow: verify email+password ONCE at login, via
  the lean `supabase-auth` package (not the full `supabase` bundle).
- After that one verification, Flask's own signed session cookie
  (`flask.session`, needs `SECRET_KEY`) is the only thing checked on every
  subsequent request — no ongoing JWT verification, no refresh-token dance.
- Roles live in a `profiles` table in the same Postgres DB:
  `id uuid references auth.users(id)`, `email`, `role check (role in
  ('admin','editor','approver'))`. Populated by hand for the ~4 known
  users via the Supabase dashboard — no signup flow.

**Permission matrix:**

| Action | admin | editor (Birthe) | approver (other reviewers) |
|---|---|---|---|
| Edit translation text | ✅ | ✅ | ❌ read-only |
| Approve / unapprove | ✅ | ✅ | ✅ |
| Draft with AI (single + bulk) | ✅ | ✅ | ❌ |
| Skip toggle | ✅ | ✅ | ❌ |
| Delete key | ✅ | ❌ | ❌ |
| Glossary / Style guide edit | ✅ | ✅ | ❌ (tab not shown) |
| Import tab | ✅ | ❌ (not shown) | ❌ (not shown) |
| CSV export / twig export | ✅ | ✅ | ✅ (read-only, no reason to block) |
| CSV import (writes drafts) | ✅ | ✅ | ❌ |
| Mark complete / Reopen | ✅ | ✅ | ❌ |
| Publish toggle (marks live on the real site) | ✅ | ❌ | ❌ |

**To build, when picked back up:**
- `app/auth.py`: real session-reading `require_user()` + a `require_role()`
  helper; keep `AUTH_MODE=mock` working for local dev.
- New: `app/routes/ui_auth.py` (`/login`, `/logout`), a plain `login.html`,
  the `profiles` table migration.
- A single `before_request` hook redirecting unauthenticated visits to
  `/login` (normal redirect for full page loads, `HX-Redirect` header for
  htmx fragment requests) — also a chance to replace the ~30 routes'
  repeated `if not require_user(request): return "Unauthorized", 401` with
  one central check.
- New env vars: `SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`.
- Replace the `translation_keys.py` DELETE route's `APP_ENV != "production"`
  placeholder gate with a real `role == "admin"` check.
- Hide Import/Glossary/Style guide nav tabs per the matrix above; make
  translation textareas read-only and hide Draft/Skip/Delete controls for
  the approver role.

## Local dev

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL (or point at local Docker Postgres)
python3 run.py          # serves on http://localhost:5001
```

`DATABASE_URL` can point at the same local Docker Postgres the original
project already runs (`docker compose up -d` there — same schema, same data),
or at the real Supabase database via `.env`'s `DATABASE_URL_PROD` +
`USE_PROD_DB=true`.

## Layout

```
app/
  __init__.py         # app factory, registers all blueprints
  db.py               # connection pool
  auth.py             # auth shim — swap this one file for real auth later
  drafting.py         # shared AI-drafting core (single-key + batch both use this)
  extraction.py       # Twig/HTML key extraction
  queries.py          # shared DB-fetching helpers — used by both /api/* JSON
                       # routes and the server-rendered ui_* routes
  routes/
    ui_translations.py  # Translations/Completed tabs — the bulk of the app
    ui_glossary.py
    ui_style_guide.py
    ui_import.py
    pages.py, translations.py, ...   # legacy /api/* JSON routes, unused by the UI
  templates/
    base.html           # shell: nav tabs, htmx + Tailwind/DaisyUI CDN, hx-boost
    translations.html, _translations_panel.html, _key_card.html, _page_list.html
    glossary.html, _glossary_row.html
    style_guide.html
    import.html, _import_form.html, _import_row.html
    _history.html
prompts/              # shared AI-drafting prompt templates
requirements.txt
run.py                # local dev entry point
```

## htmx patterns used

- `hx-boost="true"` on `<body>` — sidebar/tab links are plain `<a href>`,
  progressively enhanced into AJAX navigation.
- Debounced autosave — `hx-trigger="keyup changed delay:600ms"` on text
  fields, `hx-swap="none"` (nothing to re-render, just save), with a small
  toast via the `HX-Trigger` response header instead of touching the DOM.
- Per-key actions (approve/skip/delete/draft) — `hx-target` the key's own
  card, `hx-swap="outerHTML"`, server returns the freshly-rendered card.
- Mark complete/reopen — server responds with `HX-Redirect` to send the
  browser to whichever tab the page now belongs on, instead of trying to
  manually patch two different tabs' DOM from one response.
- Add-row/delete-row in the Import tab — delete is a one-line
  `hx-on:click="this.closest('tr').remove()"` (no server round trip needed
  for a pure client-side removal); add-row is a real htmx request since the
  server needs to render a fresh blank row.
