"""Port of netlify/functions/db-target.js.

GET /api/db-target -> { usingProdDb: boolean }
Lets the frontend show a warning banner when this server is pointed at the
real production database instead of local Docker Postgres.
"""

from flask import Blueprint, request, jsonify

from ..db import using_prod_db
from ..auth import require_user

bp = Blueprint("db_target", __name__)


@bp.route("/api/db-target", methods=["GET"])
def get_db_target():
    user = require_user(request)
    if not user:
        return "Unauthorized — please log in", 401

    return jsonify({"usingProdDb": using_prod_db()})
