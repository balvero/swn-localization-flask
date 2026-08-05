"""Server-rendered Translations/Completed tabs — htmx UI on top of the same
DB access as the JSON /api/* routes (see ../queries.py)."""

import csv
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from flask import Blueprint, request, render_template, jsonify, Response

from ..db import get_cursor
from ..auth import require_user
from ..queries import list_pages, get_page_sections, get_key_card, LANGS
from ..drafting import draft_batch, get_client
from ..jobs import create_job, update_job, get_job, cleanup_job, get_active_jobs

bp = Blueprint("ui_translations", __name__)

LANG_NAMES = {"da": "Danish", "sv": "Swedish", "no": "Norwegian"}
FLAG_BG = {
    "da": "bg-red-600 hover:bg-red-700",
    "sv": "bg-blue-600 hover:bg-blue-700",
    "no": "bg-rose-800 hover:bg-rose-900",
}
MAX_DRAFT_BATCH_SIZE = 20
MIN_BATCH_INTERVAL_SECONDS = 13


def _toast_header(message, error=False):
    # Gemini's SDK exceptions stringify to the whole nested error dict
    # (code, message, a details array with doc URLs and quota metric names)
    # — hundreds of characters. Truncate to the actually-readable part; the
    # full text is still visible in the server logs if ever needed.
    if len(message) > 200:
        message = message[:200] + "…"
    # json.dumps, not a hand-built string — Gemini error text can contain
    # quotes/special characters that would otherwise break the header.
    return {"HX-Trigger": json.dumps({"toast": {"message": message, "error": error}})}


def _is_dev():
    return os.environ.get("APP_ENV", "development") != "production"


def _sorted_pages(tab_base, sort_by):
    is_completed_tab = tab_base == "/completed"
    pages = [p for p in list_pages() if p["completed"] == is_completed_tab]
    pages.sort(key=lambda p: (-p["missing_count"] if sort_by == "missing" else 0, p["label"]))
    return pages


def _infer_view_from_referrer():
    """Best-effort (tab_base, active_slug, sort_by) from the Referer header
    — used by actions that don't otherwise know which page/tab/sort the
    user is currently looking at (e.g. a single-key draft, whose URL is
    just /translations/keys/<id>/<lang>/draft with no page context)."""
    parsed = urlparse(request.referrer or "")
    tab_base = "/completed" if parsed.path.startswith("/completed") else "/translations"
    parts = parsed.path.rstrip("/").split("/")
    active_slug = parts[-1] if len(parts) > 2 else None
    default_sort = "missing" if tab_base == "/translations" else "name"
    sort_by = parse_qs(parsed.query).get("sort", [default_sort])[0]
    return tab_base, active_slug, sort_by


def _sidebar_oob_html(tab_base=None, active_slug=None, sort_by=None):
    """Renders the sidebar as an out-of-band fragment reflecting current
    missing/approved counts — appended to a drafting response so the
    sidebar updates without the user having to switch pages or reload."""
    ref_tab_base, ref_slug, ref_sort = _infer_view_from_referrer()
    tab_base = tab_base or ref_tab_base
    active_slug = active_slug if active_slug is not None else ref_slug
    sort_by = sort_by or ref_sort
    pages = _sorted_pages(tab_base, sort_by)
    return render_template(
        "_page_list.html",
        pages=pages,
        total_missing=sum(p["missing_count"] for p in pages),
        active_slug=active_slug,
        sort_by=sort_by,
        tab_base=tab_base,
        LANGS=LANGS,
        FLAG_BG=FLAG_BG,
        oob=True,
    )


def _redirect_if_left_tab():
    """After approve/unapprove — the only action that can flip a page's
    derived `completed` state — check whether the page currently being
    viewed still belongs on the tab it's being viewed from. If it just
    crossed the boundary (e.g. approving the last translation completed it
    while looking at Translations, or unapproving one un-completed it while
    looking at Completed), its sidebar entry would vanish on its own, but
    the panel would keep showing that now-out-of-place page's content with
    nothing to replace it. Returns a (html, status, headers) tuple that
    retargets the response to refresh the whole main content area instead
    (auto-selecting whichever page is now first for this tab, or the empty
    state), or None if the page still belongs where it is."""
    tab_base, active_slug, sort_by = _infer_view_from_referrer()
    if not active_slug:
        return None

    pages = _sorted_pages(tab_base, sort_by)
    if any(p["slug"] == active_slug for p in pages):
        return None

    html = render_template("_main_content.html", **_build_ctx(tab_base, None, sort_by=sort_by))
    return html, 200, {"HX-Retarget": "#main-content", "HX-Reswap": "outerHTML"}


def _matches_filter(full_key, k, filter_text, filter_status):
    if filter_text:
        needle = filter_text.lower()
        if needle not in full_key.lower() and needle not in k["en"].lower():
            return False
    if filter_status != "all":
        if k["skip"]:
            return False
        states = [k["translations"].get(code, {"text": "", "status": "draft"}) for code, _ in LANGS]
        if filter_status == "missing":
            return any(not s["text"] for s in states)
        if filter_status == "draft":
            return any(s["text"] and s["status"] != "approved" for s in states)
        if filter_status == "approved":
            return all(s["text"] and s["status"] == "approved" for s in states)
    return True


def _render_tab(tab_base, slug):
    """Shared by /translations, /translations/<slug>, /completed,
    /completed/<slug>. Renders the full page normally, or just the panel
    fragment for htmx-triggered filter/sort/action requests."""
    is_completed_tab = tab_base == "/completed"
    sort_by = request.args.get("sort", "missing" if not is_completed_tab else "name")
    filter_text = request.args.get("q", "")
    filter_status = request.args.get("status", "all")

    pages = _sorted_pages(tab_base, sort_by)
    total_missing = sum(p["missing_count"] for p in pages)

    active_slug = slug or (pages[0]["slug"] if pages else None)
    page = next((p for p in pages if p["slug"] == active_slug), None)

    sections = {}
    lang_is_complete = {code: True for code, _ in LANGS}
    if page:
        raw_sections = get_page_sections(active_slug)
        for code, _ in LANGS:
            missing = any(
                not k["skip"] and not k["translations"].get(code, {}).get("text")
                for sec in raw_sections.values()
                for k in sec["keys"].values()
            )
            lang_is_complete[code] = not missing
        for sec_slug, sec in raw_sections.items():
            filtered_keys = {
                fk: k for fk, k in sec["keys"].items()
                if _matches_filter(fk, k, filter_text, filter_status)
            }
            sections[sec_slug] = {"label": sec["label"], "keys": filtered_keys}

    ctx = dict(
        active_tab="completed" if is_completed_tab else "translations",
        tab_base=tab_base,
        pages=pages,
        total_missing=total_missing,
        active_slug=active_slug,
        sort_by=sort_by,
        page=page,
        sections=sections,
        lang_is_complete=lang_is_complete,
        filter_text=filter_text,
        filter_status=filter_status,
        LANGS=LANGS,
        LANG_NAMES=LANG_NAMES,
        FLAG_BG=FLAG_BG,
        is_dev=_is_dev(),
        active_jobs=get_active_jobs(active_slug) if active_slug else {},
    )

    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        # A plain htmx action (filter change, publish toggle, etc.) — just
        # the panel fragment. Boosted link/form navigation (hx-boost) still
        # wants the full page so the sidebar/tab nav updates too.
        return render_template("_translations_panel.html", **ctx)
    return render_template("translations.html", **ctx)


def _build_ctx(tab_base, slug, sort_by="missing"):
    """Same context-building _render_tab does, minus the response-shape
    decision — used by the draft-all status endpoint to render the panel
    as an out-of-band refresh once a background job finishes, and by
    _redirect_if_left_tab to rebuild the whole main content area."""
    is_completed_tab = tab_base == "/completed"
    pages = _sorted_pages(tab_base, sort_by)
    active_slug = slug or (pages[0]["slug"] if pages else None)
    page = next((p for p in pages if p["slug"] == active_slug), None)
    sections = get_page_sections(active_slug) if page else {}
    lang_is_complete = {code: True for code, _ in LANGS}
    if sections:
        for code, _ in LANGS:
            missing = any(
                not k["skip"] and not k["translations"].get(code, {}).get("text")
                for sec in sections.values()
                for k in sec["keys"].values()
            )
            lang_is_complete[code] = not missing
    return dict(
        active_tab="completed" if is_completed_tab else "translations",
        tab_base=tab_base,
        pages=pages,
        total_missing=sum(p["missing_count"] for p in pages),
        active_slug=active_slug,
        sort_by=sort_by,
        page=page,
        sections=sections,
        lang_is_complete=lang_is_complete,
        filter_text="",
        filter_status="all",
        LANGS=LANGS,
        LANG_NAMES=LANG_NAMES,
        FLAG_BG=FLAG_BG,
        is_dev=_is_dev(),
        active_jobs=get_active_jobs(active_slug) if active_slug else {},
    )


@bp.route("/translations")
@bp.route("/translations/<path:slug>")
def translations_tab(slug=None):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    return _render_tab("/translations", slug)


@bp.route("/completed")
@bp.route("/completed/<path:slug>")
def completed_tab(slug=None):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    return _render_tab("/completed", slug)


# ---- Per-key actions ----

@bp.route("/translations/keys/<int:key_id>/skip", methods=["PUT"])
def toggle_skip(key_id):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    skip = request.values.get("skip") == "true"
    with get_cursor() as (conn, cur):
        cur.execute("update translation_keys set skip = %s where id = %s", (skip, key_id))
    k = get_key_card(key_id)
    html = render_template("_key_card.html", k=k, LANGS=LANGS, is_dev=_is_dev())
    # Skip changes which keys count toward total/missing at all, so the
    # sidebar's counts and progress bar go stale without this.
    return html + _sidebar_oob_html()


@bp.route("/translations/keys/<int:key_id>", methods=["DELETE"])
def delete_key(key_id):
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401
    if not _is_dev():
        return "Deleting keys is only available outside production", 403
    with get_cursor() as (conn, cur):
        cur.execute("delete from translation_keys where id = %s", (key_id,))
    # Card itself gets outerHTML-swapped with nothing (disappears); sidebar
    # OOB refresh since deleting a key also changes the page's total count.
    return "" + _sidebar_oob_html()


@bp.route("/translations/keys/<int:key_id>/<lang>/save", methods=["PUT"])
def save_translation(key_id, lang):
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401
    text = request.values.get("text", "")
    status = request.values.get("status", "draft")
    if not text.strip():
        # Matches translations.js's write-side guard: never save empty text
        # (an approved-but-blank row must fall back to English on export).
        return "", 204, {"HX-Trigger": '{"toast": {"message": "Can\'t save empty text", "error": true}}'}

    with get_cursor() as (conn, cur):
        cur.execute(
            """
            insert into translations (key_id, lang, text, status, updated_by, updated_at)
            values (%s, %s, %s, %s, %s, now())
            on conflict (key_id, lang)
            do update set text = %s, status = %s, updated_by = %s, updated_at = now()
            """,
            (key_id, lang, text, status, user["email"], text, status, user["email"]),
        )
    return "", 204, {"HX-Trigger": '{"toast": {"message": "Saved"}}'}


@bp.route("/translations/keys/<int:key_id>/<lang>/approve", methods=["PUT"])
def approve_translation(key_id, lang):
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401
    approve = request.values.get("approve") == "true"

    with get_cursor() as (conn, cur):
        cur.execute("select text from translations where key_id = %s and lang = %s", (key_id, lang))
        row = cur.fetchone()
        current_text = row[0] if row else ""
        cur.execute(
            """
            insert into translations (key_id, lang, text, status, updated_by, updated_at)
            values (%s, %s, %s, %s, %s, now())
            on conflict (key_id, lang)
            do update set status = %s, updated_by = %s, updated_at = now()
            """,
            (key_id, lang, current_text, "approved" if approve else "draft", user["email"],
             "approved" if approve else "draft", user["email"]),
        )
    # Approve/unapprove is the only action that can flip a page's derived
    # `completed` state — check that BEFORE the normal response, since if
    # the page just left the tab being viewed, we need to replace the whole
    # main content area instead of just this one key card.
    redirected = _redirect_if_left_tab()
    if redirected:
        return redirected

    k = get_key_card(key_id)
    html = render_template("_key_card.html", k=k, LANGS=LANGS, is_dev=_is_dev())
    # Approve/unapprove drives the sidebar's approved_count, i.e. its % bar.
    return html + _sidebar_oob_html()


@bp.route("/translations/keys/<int:key_id>/<lang>/draft", methods=["POST"])
def draft_one(key_id, lang):
    if not require_user(request):
        return "Unauthorized — please log in", 401

    succeeded, error = [], None
    try:
        client = get_client()
        with get_cursor() as (conn, cur):
            succeeded, failed, error = draft_batch(client, cur, [key_id], lang)
    except RuntimeError as err:
        error = (500, str(err))

    k = get_key_card(key_id)
    html = render_template("_key_card.html", k=k, LANGS=LANGS, is_dev=_is_dev())
    # Sidebar's missing/approved counts only actually change on success, but
    # the query is cheap and this keeps the response uniform either way.
    html += _sidebar_oob_html()

    if error:
        headers = _toast_header(f"Draft failed: {error[1]}", error=True)
    elif key_id in succeeded:
        headers = _toast_header(f"Drafted {LANG_NAMES[lang]} translation")
    else:
        headers = _toast_header("Draft failed — Gemini returned nothing usable for this key", error=True)

    return html, 200, headers


@bp.route("/translations/keys/<int:key_id>/<lang>/history")
def key_history(key_id, lang):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    with get_cursor() as (conn, cur):
        cur.execute(
            """
            select old_text, new_text, old_status, new_status, changed_by, changed_at
            from translation_revisions where key_id = %s and lang = %s order by changed_at desc
            """,
            (key_id, lang),
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    return render_template("_history.html", rows=rows)


# ---- Page-level actions ----

@bp.route("/translations/<path:slug>/complete", methods=["PUT"])
def mark_complete(slug):
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401
    completed = request.values.get("completed") == "true"
    with get_cursor() as (conn, cur):
        cur.execute(
            "update pages set completed = %s, completed_by = %s, completed_at = %s where slug = %s",
            (completed, user["email"] if completed else None, datetime.now(timezone.utc) if completed else None, slug),
        )
    dest = f"/completed/{slug}" if completed else f"/translations/{slug}"
    return "", 200, {"HX-Redirect": dest}


@bp.route("/translations/<path:slug>/publish/<lang>", methods=["PUT"])
def mark_published(slug, lang):
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401
    if lang not in ("da", "sv", "no"):
        return "lang must be one of: da, sv, no", 400
    published = request.values.get("published") == "true"
    with get_cursor() as (conn, cur):
        cur.execute(
            f"update pages set {lang}_published_by = %s, {lang}_published_at = %s where slug = %s",
            (user["email"] if published else None, datetime.now(timezone.utc) if published else None, slug),
        )
    tab_base = "/completed" if request.referrer and "/completed" in request.referrer else "/translations"
    # Sidebar shows a per-language published dot for every page, so it goes
    # stale the same way the missing-count did for skip/delete.
    return _render_tab(tab_base, slug) + _sidebar_oob_html(tab_base=tab_base, active_slug=slug)


def _draft_button_html(code, slug, job_id=None, done=0, total=0, oob=True, is_complete=False):
    return render_template(
        "_draft_button.html", code=code, slug=slug, job_id=job_id, done=done, total=total,
        oob=oob, LANG_NAMES=LANG_NAMES, FLAG_BG=FLAG_BG, is_complete=is_complete,
    )


def _run_draft_job(job_id, lang, batches):
    try:
        client = get_client()
    except RuntimeError as err:
        update_job(job_id, status="error", error=str(err))
        return

    last_started = 0
    done = 0
    total_failed = 0
    for batch in batches:
        elapsed = time.monotonic() - last_started
        if last_started and elapsed < MIN_BATCH_INTERVAL_SECONDS:
            time.sleep(MIN_BATCH_INTERVAL_SECONDS - elapsed)
        last_started = time.monotonic()
        batch_error_message = None
        try:
            # One connection per batch (not one for the whole job) — so a
            # later batch failing doesn't roll back translations already
            # committed by earlier ones.
            with get_cursor() as (conn, cur):
                _succeeded, failed, batch_error = draft_batch(client, cur, batch, lang)
            if batch_error:
                total_failed += len(batch)
                batch_error_message = batch_error[1]
            else:
                total_failed += len(failed)
        except Exception as err:
            total_failed += len(batch)  # never crash the whole run over one bad batch
            batch_error_message = str(err)
        done += len(batch)
        update_job(job_id, done=done, failed=total_failed, error=batch_error_message)

    update_job(job_id, status="done")


def _start_draft_all(slug, lang):
    sections = get_page_sections(slug)
    targets = []
    for sec in sections.values():
        targets.extend(
            k["key_id"] for k in sec["keys"].values()
            if not k["skip"] and not k["translations"].get(lang, {}).get("text")
        )
    batches = [targets[i : i + MAX_DRAFT_BATCH_SIZE] for i in range(0, len(targets), MAX_DRAFT_BATCH_SIZE)]

    total = sum(len(b) for b in batches)
    if not batches:
        return None, 0

    job_id = create_job(total, slug=slug, lang=lang)
    threading.Thread(target=_run_draft_job, args=(job_id, lang, batches), daemon=True).start()
    return job_id, total


@bp.route("/translations/<path:slug>/draft-all/<lang>", methods=["POST"])
def draft_all_for_lang(slug, lang):
    if not require_user(request):
        return "Unauthorized — please log in", 401

    if lang == "all":
        html_parts = []
        started = 0
        for code, _ in LANGS:
            job_id, total = _start_draft_all(slug, code)
            if job_id:
                started += 1
                html_parts.append(_draft_button_html(code, slug, job_id=job_id, done=0, total=total, oob=True))
        if started == 0:
            headers = _toast_header("Nothing to draft — every key already has translations for all languages")
            return "", 200, headers
        return "".join(html_parts), 200, _toast_header("Started drafting all languages")

    job_id, total = _start_draft_all(slug, lang)
    if not job_id:
        headers = _toast_header(f"Nothing to draft — every key already has a {LANG_NAMES[lang]} translation")
        return _draft_button_html(lang, slug, is_complete=True), 200, headers

    return _draft_button_html(lang, slug, job_id=job_id, done=0, total=total)


@bp.route("/translations/<path:slug>/draft-all/<lang>/status/<job_id>")
def draft_all_status(slug, lang, job_id):
    if not require_user(request):
        return "Unauthorized — please log in", 401

    job = get_job(job_id)
    if not job or job["status"] != "running":
        tab_base = "/completed" if request.referrer and "/completed" in request.referrer else "/translations"
        # Job's done (or errored, or vanished) — revert the button to idle
        # and refresh the whole panel out-of-band so updated translations
        # actually show up, without disturbing anything else on the page
        # the way a full targeted re-render would have.
        sections = get_page_sections(slug)
        is_complete = not any(
            not k["skip"] and not k["translations"].get(lang, {}).get("text")
            for sec in sections.values()
            for k in sec["keys"].values()
        )
        button_html = _draft_button_html(lang, slug, is_complete=is_complete)
        panel_html = render_template("_translations_panel.html", oob=True, **_build_ctx(tab_base, slug))
        sidebar_html = _sidebar_oob_html(tab_base=tab_base, active_slug=slug)

        if not job:
            headers = _toast_header("Draft all: lost track of progress (server restarted?)", error=True)
        elif job["status"] == "error":
            headers = _toast_header(f"Draft all failed: {job['error']}", error=True)
        elif job["failed"] > 0:
            reason = f" ({job['error']})" if job["error"] else ""
            headers = _toast_header(
                f"Drafted {job['total'] - job['failed']}/{job['total']} {LANG_NAMES[lang]} translations "
                f"— {job['failed']} failed{reason}",
                error=True,
            )
        else:
            headers = _toast_header(f"Drafted all {job['total']} {LANG_NAMES[lang]} translations")

        cleanup_job(job_id)
        return button_html + panel_html + sidebar_html, 200, headers

    return _draft_button_html(lang, slug, job_id=job_id, done=job["done"], total=job["total"])


@bp.route("/translations/<path:slug>/export-csv")
def export_csv(slug):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    sections = get_page_sections(slug)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["key_id", "key", "english"] + [label for _, label in LANGS])
    for sec in sections.values():
        for full_key, k in sec["keys"].items():
            if k["skip"]:
                continue
            writer.writerow(
                [k["key_id"], full_key, k["en"]]
                + [k["translations"].get(code, {}).get("text", "") for code, _ in LANGS]
            )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={slug}_translations.csv"},
    )


@bp.route("/translations/<path:slug>/import-csv", methods=["POST"])
def import_csv(slug):
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401
    file = request.files.get("csv_file")
    if not file:
        return "No file uploaded", 400

    text = file.read().decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    header, data_rows = (rows[0], rows[1:]) if rows else ([], [])

    applied = 0
    with get_cursor(commit=False) as (conn, cur):
        for row in data_rows:
            if len(row) < 3:
                continue
            key_id = row[0]
            for i, (code, _) in enumerate(LANGS):
                text_val = row[3 + i] if len(row) > 3 + i else ""
                if not key_id or not text_val:
                    continue
                cur.execute(
                    """
                    insert into translations (key_id, lang, text, status, updated_by, updated_at)
                    values (%s, %s, %s, 'draft', %s, now())
                    on conflict (key_id, lang)
                    do update set text = %s, status = 'draft', updated_by = %s, updated_at = now()
                    """,
                    (key_id, code, text_val, user["email"], text_val, user["email"]),
                )
                applied += 1
        conn.commit()

    tab_base = "/completed" if request.referrer and "/completed" in request.referrer else "/translations"
    return _render_tab(tab_base, slug)


@bp.route("/translations/<path:slug>/build/<lang>")
def build_download(slug, lang):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    if lang not in ("da", "sv", "no"):
        return "lang must be one of: da, sv, no", 400

    with get_cursor() as (conn, cur):
        cur.execute("select id, template_body from pages where slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            return f"No page found for slug '{slug}'", 404
        page_id, template_body = row
        if not template_body:
            return f"Page '{slug}' has no stored template", 409

        cur.execute(
            """
            select s.slug as section_slug, k.key, k.en_text, k.skip, t.text, t.status
            from pages p
            join sections s on s.page_id = p.id
            join translation_keys k on k.section_id = s.id
            left join translations t on t.key_id = k.id and t.lang = %s
            where p.id = %s
            """,
            (lang, page_id),
        )
        columns = [d[0] for d in cur.description]
        key_rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    content = template_body
    for row in key_rows:
        full_key = f"{row['section_slug']}.{row['key']}"
        pattern = re.compile(r"\{\{\s*t\." + re.escape(full_key) + r"\s*\}\}")
        if row["skip"]:
            value = row["en_text"]
        elif row["text"] and row["status"] == "approved":
            value = row["text"]
        elif row["text"]:
            value = row["text"]
        else:
            value = row["en_text"]
        content = pattern.sub(lambda m, v=value: v, content)

    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={slug}_{lang}.twig"},
    )
