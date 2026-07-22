"""Port of netlify/functions/translations.js.

GET /api/translations?page=homepage
PUT /api/translations   body: { keyId, lang, text, status }
"""

from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("translations", __name__)


@bp.route("/api/translations", methods=["GET"])
def get_translations():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    page_slug = request.args.get("page")
    if not page_slug:
        return "Missing ?page= parameter", 400

    with get_cursor() as (conn, cur):
        cur.execute(
            """
            select
              s.slug as section_slug, s.label as section_label,
              k.id as key_id, k.key, k.en_text, k.skip,
              t.lang, t.text, t.status
            from pages p
            join sections s on s.page_id = p.id
            join translation_keys k on k.section_id = s.id
            left join translations t on t.key_id = k.id
            where p.slug = %s
            order by s.id, k.id, t.lang
            """,
            (page_slug,),
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    return jsonify(rows)


@bp.route("/api/translations", methods=["PUT"])
def put_translation():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    key_id = body.get("keyId")
    lang = body.get("lang")
    text = body.get("text")
    status = body.get("status")
    if not key_id or not lang or not text or not status:
        return "Missing required fields", 400

    # updated_by comes from the verified identity, never the request body —
    # this is what makes the revision history (written by a DB trigger,
    # unchanged from the existing schema) trustworthy.
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

    return jsonify({"ok": True})
