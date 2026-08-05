import re
from bs4 import BeautifulSoup, NavigableString

html = """<ol class="terms-list text-size-normal"><li>
                            In this DPA:
                            <br><br>
                            i) "Company" means Platform 21 Limited trading as
                            ShopWired.
                            <br><br>
                            ii) "Customer" means Hello <strong>World</strong>.
</li></ol>"""
soup = BeautifulSoup(html, "lxml")
for el in soup.find_all(["li", "strong"]):
    groups = []
    current_group = []
    for c in el.contents:
        if isinstance(c, NavigableString):
            current_group.append(c)
        else:
            if current_group:
                groups.append(current_group)
                current_group = []
    if current_group:
        groups.append(current_group)
        
    for i, group in enumerate(groups):
        text = "".join(str(c) for c in group).strip()
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 3:
            continue
            
        print("KEY:", text)
        placeholder = f"{{{{ t.key_{i} }}}}"
        for j, c in enumerate(group):
            c.replace_with(placeholder if j == 0 else "")

print(soup.decode_contents())
