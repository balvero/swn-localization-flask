"""Shared data-fetching helpers, reused by both the JSON /api/* routes and
the server-rendered /translations, /completed, etc. UI routes — one SQL
implementation, two response formats (jsonify vs render_template)."""

from psycopg2.extras import execute_values

from .db import get_cursor

LANGS = [("da", "Dansk"), ("sv", "Svenska"), ("no", "Norsk")]


def list_pages():
    with get_cursor() as (conn, cur):
        cur.execute(
            """
            select
              p.slug,
              p.label,
              p.completed as manually_completed,
              p.completed_by,
              p.completed_at,
              p.da_published_by, p.da_published_at,
              p.sv_published_by, p.sv_published_at,
              p.no_published_by, p.no_published_at,
              count(distinct t.id) filter (where t.status = 'approved' and t.text <> '' and not k.skip) as approved_count,
              count(distinct t.id) filter (where t.text <> '' and not k.skip) as translated_count,
              count(distinct k.id) filter (where not k.skip) * 3 as total_count
            from pages p
            join sections s on s.page_id = p.id
            join translation_keys k on k.section_id = s.id
            left join translations t on t.key_id = k.id
            group by p.id
            order by p.label
            """
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    for r in rows:
        approved_count = int(r["approved_count"])
        total_count = int(r["total_count"])
        r["completed"] = bool(r["manually_completed"]) or (approved_count == total_count and total_count > 0)
        r["missing_count"] = total_count - int(r["translated_count"])

    return rows


def get_page_sections(page_slug):
    """Returns an ordered dict: {section_slug: {label, keys: {fullKey: {...}}}}"""
    with get_cursor() as (conn, cur):
        cur.execute(
            """
            select
              s.slug as section_slug, s.label as section_label,
              k.id as key_id, k.key, k.en_text, k.skip,
              t.lang, t.text, t.status
            from pages p
            join sections s on s.page_id = p.id
            join translation_keys k on k.section_id = s.id
            left join translations t on t.key_id = k.id
            where p.slug = %s
            order by s.id, k.id, t.lang
            """,
            (page_slug,),
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    sections = {}
    for r in rows:
        if r["section_slug"] not in sections:
            sections[r["section_slug"]] = {"label": r["section_label"], "keys": {}}
        full_key = r["section_slug"] + "." + r["key"]
        keys = sections[r["section_slug"]]["keys"]
        if full_key not in keys:
            keys[full_key] = {
                "key_id": r["key_id"],
                "full_key": full_key,
                "en": r["en_text"],
                "skip": r["skip"],
                "translations": {},
            }
        if r["lang"]:
            keys[full_key]["translations"][r["lang"]] = {"text": r["text"], "status": r["status"]}

    return sections


def get_key_card(key_id):
    """Re-fetches one key's full card data — used to re-render a single
    card fragment after a mutation (skip/delete/approve/draft/save)."""
    with get_cursor() as (conn, cur):
        cur.execute(
            """
            select
              s.slug as section_slug, k.id as key_id, k.key, k.en_text, k.skip,
              t.lang, t.text, t.status
            from translation_keys k
            join sections s on s.id = k.section_id
            left join translations t on t.key_id = k.id
            where k.id = %s
            """,
            (key_id,),
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    if not rows:
        return None

    first = rows[0]
    full_key = first["section_slug"] + "." + first["key"]
    card = {
        "key_id": first["key_id"],
        "full_key": full_key,
        "en": first["en_text"],
        "skip": first["skip"],
        "translations": {},
    }
    for r in rows:
        if r["lang"]:
            card["translations"][r["lang"]] = {"text": r["text"], "status": r["status"]}
    return card


def get_glossary_terms():
    with get_cursor() as (conn, cur):
        cur.execute("select * from glossary_terms order by id desc")
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def commit_import(slug, label, keys, template):
    """Idempotent upsert of a page/sections/keys — existing pages/sections
    are reused, existing keys get en_text updated if the English source
    changed, new keys get inserted. Never touches `translations`. Returns
    (new_keys, updated_keys); raises on failure (nothing gets written)."""
    with get_cursor(commit=False) as (conn, cur):
        cur.execute(
            """
            insert into pages (slug, label, template_body) values (%s, %s, %s)
            on conflict (slug) do update
              set label = excluded.label,
                  template_body = coalesce(excluded.template_body, pages.template_body)
            returning id
            """,
            (slug, label, template),
        )
        page_id = cur.fetchone()[0]

        entries = []  # (section_slug, field, en_text)
        section_slugs = []
        seen_sections = set()
        for full_key, en_text in keys.items():
            section_slug, field = full_key.split(".", 1)
            if section_slug not in seen_sections:
                seen_sections.add(section_slug)
                section_slugs.append(section_slug)
            entries.append((section_slug, field, en_text))

        section_rows = execute_values(
            cur,
            """
            insert into sections (page_id, slug, label)
            values %s
            on conflict (page_id, slug) do update set label = excluded.label
            returning id, slug
            """,
            [(page_id, s, s.replace("_", " ").title()) for s in section_slugs],
            fetch=True,
        )
        section_ids = {row[1]: row[0] for row in section_rows}

        key_rows = execute_values(
            cur,
            """
            insert into translation_keys (section_id, key, en_text)
            values %s
            on conflict (section_id, key) do update set en_text = excluded.en_text
            returning (xmax = 0) as inserted
            """,
            [(section_ids[s], field, en_text) for s, field, en_text in entries],
            fetch=True,
        )
        new_keys = sum(1 for row in key_rows if row[0])
        updated_keys = len(key_rows) - new_keys

        conn.commit()

    return new_keys, updated_keys


def get_style_guide():
    with get_cursor() as (conn, cur):
        cur.execute("select content, updated_by, updated_at from style_guide order by updated_at desc limit 1")
        row = cur.fetchone()
        if row:
            columns = [d[0] for d in cur.description]
            return dict(zip(columns, row))
        return {"content": "", "updated_by": None, "updated_at": None}
