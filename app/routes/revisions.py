"""Port of netlify/functions/revisions.js.

GET /api/revisions?keyId=12&lang=da
Returns the full change history for one translation.
"""

from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("revisions", __name__)


@bp.route("/api/revisions", methods=["GET"])
def get_revisions():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    key_id = request.args.get("keyId")
    lang = request.args.get("lang")
    if not key_id or not lang:
        return "Missing keyId or lang", 400

    with get_cursor() as (conn, cur):
        cur.execute(
            """
            select old_text, new_text, old_status, new_status, changed_by, changed_at
            from translation_revisions
            where key_id = %s and lang = %s
            order by changed_at desc
            """,
            (key_id, lang),
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    return jsonify(rows)
