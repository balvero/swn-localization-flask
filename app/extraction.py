"""In-memory adaptation of scripts/extract.py (from the original app) for
use as an HTTP endpoint — takes a raw HTML/Twig string, returns
(keys dict, rewritten template string) instead of writing files.
Includes the sentinel-leak fix already applied there: a {# comment #}
sharing a text node with real copy (no tag in between) used to leak its
placeholder token into the extracted English text; fixed by stripping the
token out of the assembled text with an unanchored regex instead of only
excluding whole-node exact matches.
"""

import re
from bs4 import BeautifulSoup, NavigableString

MIN_TEXT_LENGTH = 3
SENTINEL_RE = re.compile(r"^TWIGCOMMENTPLACEHOLDER\d+ENDPLACEHOLDER$")
SENTINEL_INLINE_RE = re.compile(r"TWIGCOMMENTPLACEHOLDER\d+ENDPLACEHOLDER")

TAGS = ["h1", "h2", "h3", "h4", "p", "li", "a", "strong", "span"]


def protect_twig_comments(source):
    comments = []

    def replace(match):
        token = f"TWIGCOMMENTPLACEHOLDER{len(comments)}ENDPLACEHOLDER"
        comments.append(match.group(0))
        return token

    protected = re.sub(r"\{#.*?#\}", replace, source, flags=re.DOTALL)
    return protected, comments


def restore_twig_comments(html, comments):
    for i, original in enumerate(comments):
        html = html.replace(f"TWIGCOMMENTPLACEHOLDER{i}ENDPLACEHOLDER", original)
    return html


def slugify(text, max_words=4):
    words = re.findall(r"[a-zA-Z0-9æøåÆØÅ]+", text.lower())[:max_words]
    return "_".join(words) if words else "text"


def section_name(el):
    node = el
    while node is not None and getattr(node, "name", None):
        if node.name == "section":
            classes = node.get("class") or []
            named = next((c for c in classes if c != "section-block"), None)
            if named:
                return named.replace("-", "_")
        node = node.parent

    node = el
    while node is not None and getattr(node, "name", None):
        classes = node.get("class") if hasattr(node, "get") else None
        if classes:
            for c in classes:
                if c not in ("cell", "grid-x", "grid-container", "full", "gt-block"):
                    return c.replace("-", "_")
        node = node.parent
    return "page"


def extract_html(raw_html):
    """Returns (keys: dict, template: str)."""
    protected_html, comments = protect_twig_comments(raw_html)
    soup = BeautifulSoup(f"<div id='__extract_root__'>{protected_html}</div>", "lxml")
    root = soup.find(id="__extract_root__")

    keys = {}
    used_keys = set()

    for el in root.find_all(TAGS):
        if el.find_parent(["script", "style"]):
            continue

        groups = []
        current_group = []
        for c in el.contents:
            if isinstance(c, NavigableString) and not SENTINEL_RE.match(c.strip()):
                current_group.append(c)
            else:
                if current_group:
                    groups.append(current_group)
                    current_group = []
        if current_group:
            groups.append(current_group)

        for group in groups:
            text = "".join(str(c) for c in group).strip()
            text = SENTINEL_INLINE_RE.sub("", text)
            text = re.sub(r"\s+", " ", text).strip()

            if len(text) < MIN_TEXT_LENGTH:
                continue

            section = section_name(el)
            base_key = f"{section}.{el.name}_{slugify(text)}"
            key = base_key
            i = 2
            while key in used_keys:
                key = f"{base_key}_{i}"
                i += 1
            used_keys.add(key)

            keys[key] = text

            placeholder = f"{{{{ t.{key} }}}}"
            for idx, c in enumerate(group):
                c.replace_with(placeholder if idx == 0 else "")

    output_html = root.decode_contents()
    output_html = restore_twig_comments(output_html, comments)

    return keys, output_html
