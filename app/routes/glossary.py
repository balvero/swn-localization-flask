"""Port of netlify/functions/glossary.js.

GET    /api/glossary          -> all terms
POST   /api/glossary          -> add a term
PUT    /api/glossary          -> update a term (body includes id)
DELETE /api/glossary?id=5     -> delete a term
"""

from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("glossary", __name__)


@bp.route("/api/glossary", methods=["GET"])
def list_terms():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    with get_cursor() as (conn, cur):
        cur.execute("select * from glossary_terms order by id")
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    return jsonify(rows)


@bp.route("/api/glossary", methods=["POST"])
def add_term():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    en, da, sv, no, notes = body.get("en"), body.get("da"), body.get("sv"), body.get("no"), body.get("notes")

    with get_cursor() as (conn, cur):
        cur.execute(
            "insert into glossary_terms (en, da, sv, no, notes) values (%s, %s, %s, %s, %s) returning *",
            (en, da, sv, no, notes),
        )
        columns = [d[0] for d in cur.description]
        row = dict(zip(columns, cur.fetchone()))

    return jsonify(row), 201


@bp.route("/api/glossary", methods=["PUT"])
def update_term():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    with get_cursor() as (conn, cur):
        cur.execute(
            "update glossary_terms set en=%s, da=%s, sv=%s, no=%s, notes=%s where id=%s",
            (body.get("en"), body.get("da"), body.get("sv"), body.get("no"), body.get("notes"), body.get("id")),
        )

    return jsonify({"ok": True})


@bp.route("/api/glossary", methods=["DELETE"])
def delete_term():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    term_id = request.args.get("id")
    with get_cursor() as (conn, cur):
        cur.execute("delete from glossary_terms where id=%s", (term_id,))

    return jsonify({"ok": True})
