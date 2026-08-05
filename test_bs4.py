import re
from bs4 import BeautifulSoup, NavigableString

html = """<p>Hello <strong>World</strong>!</p>"""
soup = BeautifulSoup(html, "lxml")
for el in soup.find_all(["p", "strong"]):
    direct_texts = [
        c for c in el.contents
        if isinstance(c, NavigableString)
    ]
    if not direct_texts: continue
    
    text = "".join(str(c) for c in direct_texts).strip()
    text = re.sub(r"\s+", " ", text).strip()
    
    placeholder = f"{{{{ t.key }}}}"
    for c in direct_texts:
        c.replace_with(placeholder if c is direct_texts[0] else "")
        
print(soup.decode_contents())
