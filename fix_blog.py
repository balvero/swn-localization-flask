import sys, re
from dotenv import load_dotenv
load_dotenv()
from app.db import get_cursor

def fix_twig_logic(match):
    # replace &lt; and &gt; inside {% ... %}
    s = match.group(0)
    s = s.replace("&lt;", "<").replace("&gt;", ">")
    return s

def fix_blog_template():
    with get_cursor(commit=True) as (conn, cur):
        cur.execute("SELECT id, template_body FROM pages WHERE slug = 'blog'")
        row = cur.fetchone()
        if not row:
            print("Blog template not found")
            return
        page_id, body = row
        
        # 1. HTML-Encoded Twig Operators
        body = re.sub(r"\{%([^%]+)%\}", fix_twig_logic, body)
        
        # 2. Automated Translation Artifacts
        replacements = {
            "{{ t.blog_posts.a_item_name }}": "{{ item.name }}",
            "{{ t.blog_posts.a_item_name_2 }}": "{{ item.name }}",
            "{{ t.blog_posts.h3_post_title }}": "{{ post.title }}",
            "{{ t.blog_posts.a_html_image_post_thumbnail }}": "{{ html.image(post.thumbnail) }}",
            "{{ t.blog_posts.span_post_category_name }}": "{{ post.category.name }}",
            "{{ t.blog_posts.p_post_excerpt_striptags_truncate }}": "{{ post.excerpt|striptags|truncate }}",
            "{{ t.blog_posts.li_page }}": "{{ page }}",
            "{{ t.blog_posts.a_page }}": "{{ page }}",
            "{{ t.blog_posts.a_page_2 }}": "{{ page }}",
            "{{ t.blog_posts.span_selected_cat_theme_sw }}": "{{ category ? category.name : 'All' }} <i class=\"theme-sw-icon-down\"></i>",
            "{{ t.blog_posts.span_selected_archive_theme_sw }}": "{{ archive ? archive : 'All' }} <i class=\"theme-sw-icon-down\"></i>",
            "{{ t.blog_posts.a_theme_sw_icon_down }}": "<i class=\"theme-sw-icon-down\"></i>",
            "{{ t.blog_posts.a_theme_sw_icon_down_2 }}": "<i class=\"theme-sw-icon-down\"></i>",
            "{{ t.blog_posts.li_theme_sw_icon_down }}": "<i class=\"theme-sw-icon-down\"></i>",
            "{{ t.blog_posts.li_theme_sw_icon_down_2 }}": "<i class=\"theme-sw-icon-down\"></i>"
        }
        for old, new in replacements.items():
            body = body.replace(old, new)

        # 3. Broken Slider JSON
        body = body.replace('data-slick="{{ features_slider|json_encode }}"', "data-slick='{{ features_slider|json_encode }}'")

        # 4. CSS Class Typo
        body = body.replace('class="grid-container0 full"', 'class="grid-container full"')

        # 5. Data Attribute Typo
        body = body.replace('data-equalizer-watchs=""', 'data-equalizer-watch=""')

        cur.execute("UPDATE pages SET template_body = %s WHERE id = %s", (body, page_id))
        print("Updated blog template successfully.")

if __name__ == "__main__":
    fix_blog_template()
