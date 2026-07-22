"""Server-rendered Style guide tab."""

from flask import Blueprint, request, render_template

from ..db import get_cursor
from ..auth import require_user
from ..queries import get_style_guide

bp = Blueprint("ui_style_guide", __name__)


@bp.route("/style-guide")
def style_guide_tab():
    if not require_user(request):
        return "Unauthorized — please log in", 401
    return render_template("style_guide.html", active_tab="style", content=get_style_guide()["content"])


@bp.route("/style-guide", methods=["PUT"])
def update_style_guide():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401
    content = request.values.get("content", "")
    with get_cursor() as (conn, cur):
        cur.execute(
            "insert into style_guide (content, updated_by, updated_at) values (%s, %s, now())",
            (content, user["email"]),
        )
    return "", 204, {"HX-Trigger": '{"toast": {"message": "Saved"}}'}
