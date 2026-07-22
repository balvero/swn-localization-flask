"""Port of netlify/functions/import-page.js.

POST /api/import-page   body: { slug, label, keys: {"section.key": "text", ...}, template }
  Idempotent upsert: existing pages/sections are reused, existing keys get
  en_text updated if the English source changed, brand new keys get
  inserted. Never touches the translations table, so existing approved
  translations for unchanged keys are left alone.
"""

from flask import Blueprint, request, jsonify

from ..db import using_prod_db
from ..auth import require_user
from ..queries import commit_import

bp = Blueprint("import_page", __name__)


@bp.route("/api/import-page", methods=["POST"])
def import_page():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    slug, label, keys, template = body.get("slug"), body.get("label"), body.get("keys"), body.get("template")
    if not slug or not label or not keys:
        return "Missing slug, label, or keys", 400

    try:
        new_keys, updated_keys = commit_import(slug, label, keys, template)
    except Exception as err:
        return f"Import failed, nothing was written: {err}", 500

    return jsonify({
        "slug": slug,
        "newKeys": new_keys,
        "updatedKeys": updated_keys,
        "target": "production" if using_prod_db() else "local",
    })
