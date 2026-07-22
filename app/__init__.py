"""Flask app factory.

This is a full Flask + htmx app — server-rendered templates (see
app/templates/) are the real UI, not the original project's React frontend
(which stays untouched and unrelated to this project). The /api/* JSON
routes below are kept too, ported 1:1 from the original netlify/functions/
— they're not used by the htmx UI (which talks to the ui_* blueprints
instead, returning HTML fragments), but left in place since they're
already built, tested, and harmless to keep around.
"""

import os
from flask import Flask, redirect
from flask_cors import CORS

from .routes import (
    pages,
    translations,
    translation_keys,
    glossary,
    style_guide,
    revisions,
    build,
    db_target,
    import_translations_csv,
    extract,
    import_page,
    draft_translation,
    draft_translations_batch,
    ui_translations,
    ui_glossary,
    ui_style_guide,
    ui_import,
)


def create_app():
    app = Flask(__name__)

    # Only matters for the /api/* JSON routes now (kept for potential
    # non-browser use) — the htmx UI is same-origin, no CORS involved.
    CORS(app, resources={r"/api/*": {"origins": os.environ.get("CORS_ORIGIN", "*")}})

    for module in (
        pages,
        translations,
        translation_keys,
        glossary,
        style_guide,
        revisions,
        build,
        db_target,
        import_translations_csv,
        extract,
        import_page,
        draft_translation,
        draft_translations_batch,
        ui_translations,
        ui_glossary,
        ui_style_guide,
        ui_import,
    ):
        app.register_blueprint(module.bp)

    @app.route("/")
    def index():
        return redirect("/translations")

    @app.route("/api/health")
    def health():
        return {"ok": True}

    return app
