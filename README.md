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

**Not done yet, deliberately deferred:**
- **Auth.** `app/auth.py` is a mock-user stub (`AUTH_MODE=mock`). Real auth
  (Supabase Auth is the leading candidate, since the DB is already on
  Supabase) is a separate decision, independent of everything above.
- **Deployment.** Nothing here is deployed anywhere yet. Candidates
  discussed: Render (free tier, but cold-starts after inactivity) or
  Railway (no cold start, but no real long-term free tier).

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
