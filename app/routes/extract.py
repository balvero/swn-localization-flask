"""Port of netlify/functions/extract.js.

POST /api/extract   body: { rawContent }
  -> { keys, template, keyCount }
"""

from flask import Blueprint, request, jsonify

from ..auth import require_user
from ..extraction import extract_html

bp = Blueprint("extract", __name__)


@bp.route("/api/extract", methods=["POST"])
def extract():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    raw_content = body.get("rawContent")
    if not raw_content or not raw_content.strip():
        return "Missing rawContent", 400

    keys, template = extract_html(raw_content)

    return jsonify({"keys": keys, "template": template, "keyCount": len(keys)})
