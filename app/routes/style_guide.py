"""Port of netlify/functions/style-guide.js.

GET /api/style-guide -> current style guide content
PUT /api/style-guide -> update it (body: { content })
"""

from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("style_guide", __name__)


@bp.route("/api/style-guide", methods=["GET"])
def get_style_guide():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    with get_cursor() as (conn, cur):
        cur.execute("select content, updated_by, updated_at from style_guide order by updated_at desc limit 1")
        row = cur.fetchone()
        if row:
            columns = [d[0] for d in cur.description]
            result = dict(zip(columns, row))
        else:
            result = {"content": ""}

    return jsonify(result)


@bp.route("/api/style-guide", methods=["PUT"])
def update_style_guide():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    content = body.get("content")

    with get_cursor() as (conn, cur):
        cur.execute(
            "insert into style_guide (content, updated_by, updated_at) values (%s, %s, now())",
            (content, user["email"]),
        )

    return jsonify({"ok": True})
