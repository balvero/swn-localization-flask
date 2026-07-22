"""Port of netlify/functions/translation-keys.js.

PUT    /api/translation-keys   body: { keyId, skip }
DELETE /api/translation-keys   body: { keyId }
  Permanently removes a key and its translations/revision history — for
  orphaned duplicates left behind when re-importing a page after the
  source .twig/extraction changes a key's content-derived name. Gated on
  APP_ENV, same intent as the old app's LOCAL_DEV-only server-side check:
  the frontend hides the button in production builds too, but that alone
  was never the real boundary.
"""

import os
from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("translation_keys", __name__)


@bp.route("/api/translation-keys", methods=["PUT"])
def update_key():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    key_id = body.get("keyId")
    skip = body.get("skip")
    if not key_id or not isinstance(skip, bool):
        return "Missing keyId or skip (boolean)", 400

    with get_cursor() as (conn, cur):
        cur.execute("update translation_keys set skip = %s where id = %s", (skip, key_id))

    return jsonify({"ok": True})


@bp.route("/api/translation-keys", methods=["DELETE"])
def delete_key():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    if os.environ.get("APP_ENV", "development") == "production":
        return "Deleting keys is only available outside production", 403

    body = request.get_json(silent=True) or {}
    key_id = body.get("keyId")
    if not key_id:
        return "Missing keyId", 400

    # translations and translation_revisions both reference key_id with
    # "on delete cascade" (schema.sql), so this alone cleans up everything.
    with get_cursor() as (conn, cur):
        cur.execute("delete from translation_keys where id = %s", (key_id,))
        deleted = cur.rowcount

    if deleted == 0:
        return "Key not found", 404
    return jsonify({"ok": True})
