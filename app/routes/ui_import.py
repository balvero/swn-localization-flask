"""Server-rendered Import tab — paste raw .twig/HTML, extract, review,
commit. Mirrors ImportTab.jsx's three-step flow; the extracted
template/keys travel through the page as plain form fields (a hidden
template input + parallel keys[]/texts[] inputs) rather than server-side
session state, so no session/secret-key setup is needed for this."""

from flask import Blueprint, request, render_template

from ..auth import require_user
from ..db import using_prod_db
from ..extraction import extract_html
from ..queries import commit_import

bp = Blueprint("ui_import", __name__)


@bp.route("/import")
def import_tab():
    if not require_user(request):
        return "Unauthorized — please log in", 401
    return render_template("import.html", active_tab="import", review=None, result=None, error=None)


@bp.route("/import/extract", methods=["POST"])
def extract():
    if not require_user(request):
        return "Unauthorized — please log in", 401
    raw_content = request.values.get("raw_content", "")
    if not raw_content.strip():
        return render_template("_import_form.html", review=None, result=None, error="Paste some source first.", raw_content=raw_content)

    keys, template = extract_html(raw_content)
    return render_template("_import_form.html", review={"keys": keys, "template": template}, result=None, error=None)


@bp.route("/import/add-row", methods=["POST"])
def add_row():
    if not require_user(request):
        return "Unauthorized — please log in", 401
    return render_template("_import_row.html", key=None, text=None)


@bp.route("/import/commit", methods=["POST"])
def commit():
    if not require_user(request):
        return "Unauthorized — please log in", 401

    slug = request.form.get("slug", "").strip()
    label = request.form.get("label", "").strip()
    template = request.form.get("template")
    key_list = request.form.getlist("keys[]")
    text_list = request.form.getlist("texts[]")

    if not slug or not label:
        return render_template(
            "import.html", active_tab="import", result=None, error="Slug and label are both required.",
            review={"keys": dict(zip(key_list, text_list)), "template": template},
        )

    keys = {k.strip(): t for k, t in zip(key_list, text_list) if k.strip()}
    if not keys:
        return render_template("import.html", active_tab="import", review=None, result=None, error="No keys to import.")

    try:
        new_keys, updated_keys = commit_import(slug, label, keys, template)
    except Exception as err:
        return render_template("import.html", active_tab="import", review=None, result=None, error=f"Import failed, nothing was written: {err}")

    result = {
        "slug": slug,
        "new_keys": new_keys,
        "updated_keys": updated_keys,
        "target": "production" if using_prod_db() else "local",
    }
    return render_template("import.html", active_tab="import", review=None, result=result, error=None)
