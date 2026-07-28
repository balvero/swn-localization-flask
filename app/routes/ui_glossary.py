"""Server-rendered Glossary tab."""

from flask import Blueprint, request, render_template

from ..db import get_cursor
from ..auth import require_user
from ..queries import get_glossary_terms

bp = Blueprint("ui_glossary", __name__)


@bp.route("/glossary")
def glossary_tab():
    if not require_user(request):
        return "Unauthorized — please log in", 401
    return render_template("glossary.html", active_tab="glossary", terms=get_glossary_terms())


import json

@bp.route("/glossary", methods=["POST"])
def add_term():
    if not require_user(request):
        return "Unauthorized — please log in", 401
    try:
        with get_cursor() as (conn, cur):
            cur.execute(
                "insert into glossary_terms (en, da, sv, no, notes) values ('', '', '', '', '') returning *"
            )
            columns = [d[0] for d in cur.description]
            t = dict(zip(columns, cur.fetchone()))
        return render_template("_glossary_row.html", t=t), 200, {"HX-Trigger": json.dumps({"toast": {"message": "Added new term"}})}
    except Exception as e:
        return "Failed to add", 500, {"HX-Trigger": json.dumps({"toast": {"message": str(e), "error": True}})}


@bp.route("/glossary/<int:term_id>", methods=["PUT"])
def update_term(term_id):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    fields = {f: request.values.get(f, "") for f in ("en", "da", "sv", "no", "notes")}
    try:
        with get_cursor() as (conn, cur):
            cur.execute(
                "update glossary_terms set en=%s, da=%s, sv=%s, no=%s, notes=%s where id=%s returning *",
                (fields["en"], fields["da"], fields["sv"], fields["no"], fields["notes"], term_id),
            )
            columns = [d[0] for d in cur.description]
            t = dict(zip(columns, cur.fetchone()))
        return render_template("_glossary_row.html", t=t), 200, {"HX-Trigger": json.dumps({"toast": {"message": "Saved"}})}
    except Exception as e:
        # In case of failure, we still need to return something HTMX can swap, or a 204 if we don't want to change the row
        # We can just return 204 with the error toast so the row remains unchanged but they see the error
        return "", 204, {"HX-Trigger": json.dumps({"toast": {"message": f"Failed to save: {str(e)}", "error": True}})}


@bp.route("/glossary/<int:term_id>", methods=["DELETE"])
def delete_term(term_id):
    if not require_user(request):
        return "Unauthorized — please log in", 401
    try:
        with get_cursor() as (conn, cur):
            cur.execute("delete from glossary_terms where id=%s", (term_id,))
        return "", 200, {"HX-Trigger": json.dumps({"toast": {"message": "Deleted term"}})}
    except Exception as e:
        return "Failed to delete", 500, {"HX-Trigger": json.dumps({"toast": {"message": str(e), "error": True}})}
