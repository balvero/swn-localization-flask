"""Shared AI-drafting core, used by both routes/draft_translation.py
(single key) and routes/draft_translations_batch.py (many keys) — the
single-key route just calls this with a one-item list. Keeping ONE
implementation instead of two parallel ones (which is what the original
Netlify version had) is deliberate: that duplication is exactly what let
the two versions' "sibling tone" query limits drift out of sync (2 vs 3)
in the original app, caught during a review pass there.

Three backends, selected via DRAFT_BACKEND ("gemini", the default,
"ollama", or "openrouter"): Gemini for anything ever deployed (Birthe et
al.), Ollama for local-only drafting with no daily request quota, and
OpenRouter as a free, cloud-hosted fallback (no local model download,
but a real rate limit — see .env.example) — chosen deliberately over
Gemma's translation-specialized sibling (TranslateGemma) because that
model's fixed single-string prompt format can't carry the
glossary/style-guide/batch context this app relies on; a general
instruction-following model with real structured-output support (Ollama's
`format=<json schema>`, OpenRouter/OpenAI's `response_format.json_schema`,
same mechanism as Gemini's response_schema) can.
All three backends share the exact same prompt-building/schema/DB-write
logic below — only the "call the model" step branches.

The structured-output mode (response_schema / Ollama's format=) constrains
the model to well-formed JSON, matched back to the real key_id (never by
response position), so a batch that doesn't validate cleanly fails as a
whole rather than risking a translation silently landing on the wrong key.
"""

import json
import os
from pathlib import Path

from psycopg2.extras import execute_values

ROOT = Path(__file__).parent.parent
BATCH_TEMPLATE = (ROOT / "prompts" / "draft-translation-batch.txt").read_text(encoding="utf-8")
ITEM_TEMPLATE = (ROOT / "prompts" / "draft-translation-item.txt").read_text(encoding="utf-8")

LANG_NAMES = {"da": "Danish", "sv": "Swedish", "no": "Norwegian"}

DRAFT_BACKEND = os.environ.get("DRAFT_BACKEND", "gemini")
# e2b ("edge" 2B, ~7.2GB) — not gemma4:12b. Measured directly: 12b took 7+
# minutes on a glossary-constrained prompt and never finished naturally
# (had to be killed); e2b did the identical prompt correctly in ~33s. The
# "edge" tier is specifically built for on-device/laptop speed, and at
# this app's scale (short marketing-copy strings, not long-form writing)
# it produced a correct, natural, glossary-consistent translation despite
# being the smaller model — bigger wasn't better here.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e2b")

# Free-tier OpenRouter model — cloud-hosted, so not bottlenecked by local
# hardware, but has its own (unconfirmed-exact) per-minute/per-day rate cap.
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")

_gemini_client = None
_ollama_client = None
_openrouter_client = None


def _get_gemini_client():
    # This project isn't hosted on Netlify, so none of the GEMINI_API_KEY
    # env-var auto-interception that motivated GEMINI_API_KEY_DIRECT in the
    # old app applies here — a plain, ordinary name is fine.
    global _gemini_client
    if _gemini_client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set — see .env.example")
        from google import genai

        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _get_ollama_client():
    global _ollama_client
    if _ollama_client is None:
        import ollama

        try:
            _ollama_client = ollama.Client()  # defaults to http://localhost:11434
            _ollama_client.list()  # cheap call to fail fast if the daemon isn't running
        except Exception as err:
            raise RuntimeError(
                f"Can't reach Ollama at localhost:11434 — is `ollama serve` running? ({err})"
            )
    return _ollama_client


def _get_openrouter_client():
    global _openrouter_client
    if _openrouter_client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set — see .env.example")
        import requests

        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
        _openrouter_client = session
    return _openrouter_client


def get_client():
    """Returns whichever backend's client DRAFT_BACKEND selects — draft_batch
    below re-checks DRAFT_BACKEND itself to decide which one to call, this
    just gives callers (draft_one, _run_draft_job) an early, cheap way to
    fail fast if that backend isn't reachable at all before doing any work."""
    if DRAFT_BACKEND == "ollama":
        return _get_ollama_client()
    if DRAFT_BACKEND == "openrouter":
        return _get_openrouter_client()
    return _get_gemini_client()


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


def _call_gemini(client, prompt, num_items):
    from google.genai import types

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
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=2048 + num_items * 300,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    return response.text


def _call_ollama(client, prompt, num_items):
    # Same schema shape as Gemini's, just plain JSON Schema (lowercase type
    # strings) instead of the genai SDK's Type enum.
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "keyId": {"type": "integer"},
                "translation": {"type": "string"},
            },
            "required": ["keyId", "translation"],
        },
    }
    response = client.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        format=schema,
        think="low",  # Gemma 4's "configurable thinking modes" default to unbounded
        # reasoning when unset — harmless for a simple sentence, but scales badly
        # on a harder prompt (e.g. one with glossary constraints to follow),
        # observed taking 3+ minutes uncapped vs ~75s capped. Same fix already
        # applied to the Gemini backend (thinking_level=LOW) for the same reason:
        # this is translation, not a task that benefits from deep reasoning.
        options={"temperature": 0},  # deterministic, not creative — this is translation, not prose
    )
    return response.message.content


def _call_openrouter(client, prompt, num_items):
    # OpenAI-compatible structured outputs require an object at the schema
    # root (unlike Gemini/Ollama, which accept a bare array) — wrap the
    # array in a "translations" key and unwrap it below so draft_batch's
    # shared parsing (which expects a bare JSON array) doesn't need to
    # know which backend produced the response.
    schema = {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyId": {"type": "integer"},
                        "translation": {"type": "string"},
                    },
                    "required": ["keyId", "translation"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["translations"],
        "additionalProperties": False,
    }
    response = client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,  # deterministic, not creative — this is translation, not prose
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "translations", "strict": True, "schema": schema},
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    try:
        return json.dumps(json.loads(content).get("translations", []))
    except (json.JSONDecodeError, AttributeError, TypeError):
        # Let draft_batch's own json.loads below fail the same way it would
        # for a malformed Gemini/Ollama response — same "whole batch fails,
        # never guesses" contract either way.
        return content


def draft_batch(client, cur, key_ids, lang):
    """Returns (succeeded, failed, error) — error is a (status, message)
    tuple on hard failure (the model request itself failed), else None."""
    prompt, key_rows = build_prompt(cur, key_ids, lang)
    if not key_rows:
        return [], list(key_ids), None

    try:
        if DRAFT_BACKEND == "ollama":
            response_text = _call_ollama(client, prompt, len(key_ids))
        elif DRAFT_BACKEND == "openrouter":
            response_text = _call_openrouter(client, prompt, len(key_ids))
        else:
            response_text = _call_gemini(client, prompt, len(key_ids))
    except Exception as err:  # noqa: BLE001 — whole batch fails, never guesses
        status = getattr(err, "status_code", None) or getattr(err, "code", None) or 502
        return [], list(key_ids), (status if isinstance(status, int) else 502, str(err))

    try:
        parsed = json.loads(response_text)
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
