import sys, re
from dotenv import load_dotenv
load_dotenv()
from app.db import get_cursor

def fix_all_templates():
    with get_cursor(commit=True) as (conn, cur):
        cur.execute("SELECT id, slug, template_body FROM pages")
        rows = cur.fetchall()
        for row in rows:
            page_id, slug, body = row
            if not body:
                continue

            orig_body = body

            # 1. Malformed HTML Tags: Incorrectly nested </source>
            body = body.replace('</source>', '')

            # 2. Fatal Tag Placement & Unclosed Container:
            # A closing </div> tag was placed at the very end of the file, completely outside of any {% block %}.
            # The stray </div> tag has been moved inside the block to properly close this container.
            body = re.sub(r"\{%\s*endblock\s*%\}\s*</div>\s*$", "</div>\n{% endblock %}", body)

            # Special case for gladforvin if it was missing the closing div entirely
            if slug == 'page_case_story_gladforvin':
                if '<div class="content case-story" id="content">' in body:
                    # check if the stray div fix applied
                    if orig_body == body:
                        # it didn't apply, meaning it didn't have a stray </div> at the end, but the container is unclosed.
                        # insert </div> before the last {% endblock %}
                        last_block = body.rfind('{% endblock %}')
                        if last_block != -1:
                            body = body[:last_block] + '</div>\n' + body[last_block:]

            if body != orig_body:
                cur.execute("UPDATE pages SET template_body = %s WHERE id = %s", (body, page_id))
                print(f"Updated template for {slug}")

if __name__ == "__main__":
    fix_all_templates()
