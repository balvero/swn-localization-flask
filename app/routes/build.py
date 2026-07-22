"""Port of netlify/functions/build.js.

GET /api/build?page=homepage&lang=da
  -> bakes the page's stored template_body with that language's
     translations, substituting every {{ t.section.key }} placeholder with
     real text. Fallback rule: a key with no translation row yet, or an
     empty one even if marked approved, falls back to the English source —
     never a half-translated file with dangling {{ t.* }} tokens.
"""

import re
from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("build", __name__)

LANGS = ["da", "sv", "no"]


@bp.route("/api/build", methods=["GET"])
def build_page():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    page_slug = request.args.get("page")
    lang = request.args.get("lang")
    if not page_slug or not lang:
        return "Missing ?page= or ?lang= parameter", 400
    if lang not in LANGS:
        return "lang must be one of: da, sv, no", 400

    with get_cursor() as (conn, cur):
        cur.execute("select id, label, template_body from pages where slug = %s", (page_slug,))
        page_row = cur.fetchone()
        if not page_row:
            return f"No page found for slug '{page_slug}'", 404
        page_id, label, template_body = page_row
        if not template_body:
            return f"Page '{page_slug}' has no stored template — re-run import with --template", 409

        cur.execute(
            """
            select
              s.slug as section_slug, k.key, k.en_text, k.skip,
              t.text, t.status
            from pages p
            join sections s on s.page_id = p.id
            join translation_keys k on k.section_id = s.id
            left join translations t on t.key_id = k.id and t.lang = %s
            where p.id = %s
            """,
            (lang, page_id),
        )
        columns = [d[0] for d in cur.description]
        key_rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    content = template_body
    stats = {"total": len(key_rows), "approved": 0, "draftUsed": 0, "missing": 0, "skipped": 0}

    for row in key_rows:
        full_key = f"{row['section_slug']}.{row['key']}"
        pattern = re.compile(r"\{\{\s*t\." + re.escape(full_key) + r"\s*\}\}")

        # Skip is authoritative over any stored text. Checking row["text"]'s
        # truthiness before status matters: an approved translation that's
        # since been edited down to blank must still fall back to English.
        if row["skip"]:
            value = row["en_text"]
            stats["skipped"] += 1
        elif row["text"] and row["status"] == "approved":
            value = row["text"]
            stats["approved"] += 1
        elif row["text"]:
            value = row["text"]
            stats["draftUsed"] += 1
        else:
            value = row["en_text"]
            stats["missing"] += 1

        content = pattern.sub(lambda m, v=value: v, content)

    return jsonify({"filename": f"{page_slug}_{lang}.twig", "content": content, "stats": stats})
