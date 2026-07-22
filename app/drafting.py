"""Shared AI-drafting core, used by both routes/draft_translation.py
(single key) and routes/draft_translations_batch.py (many keys) — the
single-key route just calls this with a one-item list. Keeping ONE
implementation instead of two parallel ones (which is what the original
Netlify version had) is deliberate: that duplication is exactly what let
the two versions' "sibling tone" query limits drift out of sync (2 vs 3)
in the original app, caught during a review pass there.

Mirrors scripts/draft_translations.py's approach: Gemini's structured-output
mode (response_schema) constrains the model to well-formed JSON, matched
back to the real key_id (never by response position), so a batch that
doesn't validate cleanly fails as a whole rather than risking a
translation silently landing on the wrong key.
"""

import json
import os
from pathlib import Path

from google import genai
from google.genai import types
from psycopg2.extras import execute_values

ROOT = Path(__file__).parent.parent
BATCH_TEMPLATE = (ROOT / "prompts" / "draft-translation-batch.txt").read_text(encoding="utf-8")
ITEM_TEMPLATE = (ROOT / "prompts" / "draft-translation-item.txt").read_text(encoding="utf-8")

LANG_NAMES = {"da": "Danish", "sv": "Swedish", "no": "Norwegian"}

_client = None


def get_client():
    # This project isn't hosted on Netlify, so none of the GEMINI_API_KEY
    # env-var auto-interception that motivated GEMINI_API_KEY_DIRECT in the
    # old app applies here — a plain, ordinary name is fine.
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set — see .env.example")
        _client = genai.Client(api_key=api_key)
    return _client


def fill_template(template, values):
    result = template
    for key, value in values.items():
        result = result.replace("{{" + key + "}}", value)
    return result


def build_prompt(cur, key_ids, lang):
    cur.execute(
        "select id, key, en_text, section_id from translation_keys where id = any(%s)",
        (key_ids,),
    )
    key_rows = [dict(zip(["id", "key", "en_text", "section_id"], row)) for row in cur.fetchall()]
    if not key_rows:
        return None, key_rows

    cur.execute(
        "select key_id, text from translations where key_id = any(%s) and lang = %s",
        (key_ids, lang),
    )
    prior_by_key = dict(cur.fetchall())

    cur.execute("select en, da, sv, no, notes from glossary_terms")
    glossary_rows = [dict(zip(["en", "da", "sv", "no", "notes"], row)) for row in cur.fetchall()]

    cur.execute("select content from style_guide order by updated_at desc limit 1")
    style_row = cur.fetchone()
    style_guide = style_row[0] if style_row else ""

    section_id = key_rows[0]["section_id"]
    cur.execute(
        """
        select k.key, t.text
        from translations t
        join translation_keys k on k.id = t.key_id
        where k.section_id = %s and t.lang = %s and t.status = 'approved' and k.id != all(%s)
        limit 3
        """,
        (section_id, lang, key_ids),
    )
    memory_rows = cur.fetchall()

    item_blocks = []
    for k in key_rows:
        relevant_terms = [t for t in glossary_rows if t["en"].lower() in k["en_text"].lower()]
        prior_line = ""
        if prior_by_key.get(k["id"]):
            prior_line = (
                f'\n  Previously approved (now outdated, English changed): "{prior_by_key[k["id"]]}"'
                " — update it, preserving established phrasing where it still applies."
            )
        glossary_line = ""
        if relevant_terms:
            lines = "; ".join(
                f'"{t["en"]}" -> "{t.get(lang) or t["en"]}"' + (f' ({t["notes"]})' if t.get("notes") else "")
                for t in relevant_terms
            )
            glossary_line = f"\n  Glossary terms to use exactly: {lines}"
        item_blocks.append(
            fill_template(
                ITEM_TEMPLATE,
                {
                    "KEY_ID": str(k["id"]),
                    "KEY": k["key"],
                    "EN_TEXT": k["en_text"],
                    "PRIOR_LINE": prior_line,
                    "GLOSSARY_LINE": glossary_line,
                },
            )
        )

    memory_section = ""
    if memory_rows:
        lines = "\n".join(f'- "{key}": "{text}"' for key, text in memory_rows)
        memory_section = f"\n\nAlready-approved translations from the same section — match this tone:\n{lines}"

    prompt = fill_template(
        BATCH_TEMPLATE,
        {
            "LANG_NAME": LANG_NAMES[lang],
            "ITEMS": "\n\n".join(item_blocks),
            "MEMORY_SECTION": memory_section,
            "STYLE_GUIDE": style_guide,
        },
    ).strip()

    return prompt, key_rows


def draft_batch(client, cur, key_ids, lang):
    """Returns (succeeded, failed, error) — error is a (status, message)
    tuple on hard failure (Gemini request itself failed), else None."""
    prompt, key_rows = build_prompt(cur, key_ids, lang)
    if not key_rows:
        return [], list(key_ids), None

    schema = {
        "type": types.Type.ARRAY,
        "items": {
            "type": types.Type.OBJECT,
            "properties": {
                "keyId": {"type": types.Type.INTEGER},
                "translation": {"type": types.Type.STRING},
            },
            "required": ["keyId", "translation"],
        },
    }

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=2048 + len(key_ids) * 300,
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
    except Exception as err:  # noqa: BLE001 — whole batch fails, never guesses
        status = getattr(err, "status_code", None) or getattr(err, "code", None) or 502
        return [], list(key_ids), (status if isinstance(status, int) else 502, str(err))

    try:
        parsed = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    requested_ids = set(key_ids)
    translation_by_key_id = {}
    if isinstance(parsed, list):
        for item in parsed:
            if (
                isinstance(item, dict)
                and isinstance(item.get("keyId"), int)
                and item["keyId"] in requested_ids
                and isinstance(item.get("translation"), str)
                and item["translation"].strip()
            ):
                translation_by_key_id[item["keyId"]] = item["translation"].strip()

    succeeded = [kid for kid in key_ids if kid in translation_by_key_id]
    failed = [kid for kid in key_ids if kid not in translation_by_key_id]

    if succeeded:
        execute_values(
            cur,
            """
            insert into translations (key_id, lang, text, status, updated_by, updated_at)
            values %s
            on conflict (key_id, lang)
            do update set text = excluded.text, status = 'draft', updated_by = excluded.updated_by, updated_at = now()
            """,
            [(kid, lang, translation_by_key_id[kid]) for kid in succeeded],
            template="(%s, %s, %s, 'draft', 'ai-draft', now())",
        )

    return succeeded, failed, None
