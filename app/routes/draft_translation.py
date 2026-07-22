"""Port of netlify/functions/draft-translation.js.

POST /api/draft-translation   body: { keyId, lang }
Thin wrapper around drafting.draft_batch with a single-item list — see
app/drafting.py's module docstring for why this isn't a second parallel
implementation of the prompt/query logic.
"""

from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user
from ..drafting import draft_batch, get_client

bp = Blueprint("draft_translation", __name__)


@bp.route("/api/draft-translation", methods=["POST"])
def draft_translation():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    key_id = body.get("keyId")
    lang = body.get("lang")
    if not key_id or not lang:
        return "Missing keyId or lang", 400

    try:
        client = get_client()
    except RuntimeError as err:
        return str(err), 500

    with get_cursor() as (conn, cur):
        succeeded, failed, error = draft_batch(client, cur, [key_id], lang)

    if error:
        status, message = error
        return jsonify({"error": message}), status
    if key_id in failed:
        return "Key not found or drafting failed", 404

    return jsonify({"keyId": key_id, "lang": lang, "ok": True})
