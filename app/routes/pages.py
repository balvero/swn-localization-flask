"""Port of netlify/functions/pages.js.

GET  /api/pages
PUT  /api/pages   body: { slug, completed } or { slug, lang, published }
"""

from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("pages", __name__)

LANGS = ["da", "sv", "no"]


@bp.route("/api/pages", methods=["GET"])
def list_pages():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    with get_cursor() as (conn, cur):
        cur.execute(
            """
            select
              p.slug,
              p.label,
              p.completed as manually_completed,
              p.completed_by,
              p.completed_at,
              p.da_published_by, p.da_published_at,
              p.sv_published_by, p.sv_published_at,
              p.no_published_by, p.no_published_at,
              count(distinct t.id) filter (where t.status = 'approved' and t.text <> '' and not k.skip) as approved_count,
              count(distinct t.id) filter (where t.text <> '' and not k.skip) as translated_count,
              count(distinct k.id) filter (where not k.skip) * 3 as total_count
            from pages p
            join sections s on s.page_id = p.id
            join translation_keys k on k.section_id = s.id
            left join translations t on t.key_id = k.id
            group by p.id
            order by p.label
            """
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    for r in rows:
        approved_count = int(r["approved_count"])
        total_count = int(r["total_count"])
        r["completed"] = bool(r["manually_completed"]) or (approved_count == total_count and total_count > 0)
        r["missing_count"] = total_count - int(r["translated_count"])
        # datetimes aren't JSON-serializable by default — jsonify handles
        # this via Flask's JSON encoder, so no manual conversion needed here.

    return jsonify(rows)


@bp.route("/api/pages", methods=["PUT"])
def update_page():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    slug = body.get("slug")
    if not slug:
        return "Missing slug", 400

    has_completed = isinstance(body.get("completed"), bool)
    has_published = isinstance(body.get("published"), bool)
    if has_completed and has_published:
        return "Send completed or (lang and published), not both", 400

    if has_completed:
        completed = body["completed"]
        with get_cursor() as (conn, cur):
            cur.execute(
                "update pages set completed = %s, completed_by = %s, completed_at = %s where slug = %s",
                (completed, user["email"] if completed else None, datetime.now(timezone.utc) if completed else None, slug),
            )
        return jsonify({"ok": True})

    if has_published:
        lang = body.get("lang")
        if lang not in LANGS:
            return "lang must be one of: da, sv, no", 400
        published = body["published"]
        # Column names can't be parameterized — safe here only because lang
        # was just checked against the fixed LANGS allowlist above, never
        # interpolated straight from the request.
        with get_cursor() as (conn, cur):
            cur.execute(
                f"update pages set {lang}_published_by = %s, {lang}_published_at = %s where slug = %s",
                (user["email"] if published else None, datetime.now(timezone.utc) if published else None, slug),
            )
        return jsonify({"ok": True})

    return "Missing completed or (lang and published)", 400
