import sys
from dotenv import load_dotenv
load_dotenv()
from app.db import get_cursor

with get_cursor(commit=True) as (conn, cur):
    cur.execute("SELECT id, template_body FROM pages WHERE slug = 'partials/header'")
    row = cur.fetchone()
    if row:
        page_id = row[0]
        body = row[1]
        old_str = "{{ t.{{ category.categories ? 'parent-with-sub' }}.a_view_all }}"
        new_str = "{{ t[category.categories ? 'parent-with-sub' : 'default_key'].a_view_all }}"
        if old_str in body:
            new_body = body.replace(old_str, new_str)
            cur.execute("UPDATE pages SET template_body = %s WHERE id = %s", (new_body, page_id))
            print("Successfully updated the template_body for partials/header.")
        else:
            print("Could not find the exact old_str in template_body.")
