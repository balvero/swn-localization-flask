"""Port of netlify/functions/draft-translations-batch.js.

POST /api/draft-translations-batch   body: { keyIds: [int, ...], lang }
"""

from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user
from ..drafting import draft_batch, get_client

bp = Blueprint("draft_translations_batch", __name__)

MAX_BATCH_SIZE = 30  # sanity cap — the frontend batches much smaller (~20)


@bp.route("/api/draft-translations-batch", methods=["POST"])
def draft_translations_batch():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    key_ids = body.get("keyIds")
    lang = body.get("lang")
    if not isinstance(key_ids, list) or len(key_ids) == 0 or not lang:
        return "Missing keyIds (array) or lang", 400
    if len(key_ids) > MAX_BATCH_SIZE:
        return f"Too many keyIds — max {MAX_BATCH_SIZE} per batch", 400

    try:
        client = get_client()
    except RuntimeError as err:
        return str(err), 500

    with get_cursor() as (conn, cur):
        succeeded, failed, error = draft_batch(client, cur, key_ids, lang)

    if error:
        status, message = error
        return jsonify({"error": message}), status

    return jsonify({"succeeded": succeeded, "failed": failed})
