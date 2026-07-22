"""Port of netlify/functions/import-translations-csv.js.

POST /api/import-translations-csv   body: { rows: [{ keyId, lang, text }, ...] }
  Bulk-commits translations parsed from an uploaded CSV. Same upsert shape
  as translations.py's PUT, looped in one transaction. Always lands as
  status='draft'. Blank cells are skipped so a partially-filled-in CSV
  can't wipe out translations for keys the external tool didn't touch.
"""

from flask import Blueprint, request, jsonify

from ..db import get_cursor
from ..auth import require_user

bp = Blueprint("import_translations_csv", __name__)


@bp.route("/api/import-translations-csv", methods=["POST"])
def import_csv():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    body = request.get_json(silent=True) or {}
    rows = body.get("rows")
    if not isinstance(rows, list) or len(rows) == 0:
        return "No rows to import", 400

    applied = 0
    try:
        with get_cursor(commit=False) as (conn, cur):
            for row in rows:
                key_id, lang, text = row.get("keyId"), row.get("lang"), row.get("text")
                if not key_id or not lang or not text:
                    continue
                cur.execute(
                    """
                    insert into translations (key_id, lang, text, status, updated_by, updated_at)
                    values (%s, %s, %s, 'draft', %s, now())
                    on conflict (key_id, lang)
                    do update set text = %s, status = 'draft', updated_by = %s, updated_at = now()
                    """,
                    (key_id, lang, text, user["email"], text, user["email"]),
                )
                applied += 1
            conn.commit()
    except Exception as err:
        return f"Import failed, nothing was written: {err}", 500

    return jsonify({"applied": applied})
