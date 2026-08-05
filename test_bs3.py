import re
from bs4 import BeautifulSoup, NavigableString

html = """<p>Hello <strong>World</strong>!</p>"""
soup = BeautifulSoup(html, "lxml")
for el in soup.find_all(["p"]):
    direct_texts = [
        c for c in el.contents
        if isinstance(c, NavigableString)
    ]
    print(direct_texts)
