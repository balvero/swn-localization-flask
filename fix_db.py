import sys
from dotenv import load_dotenv
load_dotenv()
from app.db import get_cursor

with get_cursor() as (conn, cur):
    cur.execute("SELECT slug, template_body FROM pages WHERE template_body LIKE '%{{ t.{{%'")
    rows = cur.fetchall()
    for r in rows:
        print(f"SLUG: {r[0]}")
        body = r[1]
        idx = body.find('{{ t.{{')
        start = max(0, idx - 100)
        end = min(len(body), idx + 100)
        print(body[start:end])
