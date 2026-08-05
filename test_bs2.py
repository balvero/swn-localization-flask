import re
from bs4 import BeautifulSoup, NavigableString, Tag

html = """<ol class="terms-list text-size-normal"><li>
                            In this DPA:
                            <br><br>
                            i) "Company" means Platform 21 Limited trading as
                            ShopWired.
                            <br><br>
                            ii) "Customer" means the person or organisation
                            using the Service.
</li></ol>"""
soup = BeautifulSoup(html, "lxml")
for el in soup.find_all(["li"]):
    nodes = []
    for c in el.contents:
        if isinstance(c, NavigableString):
            nodes.append(c)
        elif isinstance(c, Tag) and c.name == 'br':
            nodes.append(c)
    
    if not nodes: continue
    
    text_parts = []
    for n in nodes:
        if isinstance(n, Tag):
            text_parts.append("<br>")
        else:
            text_parts.append(str(n))
            
    text = "".join(text_parts).strip()
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" <br> ", "<br>").replace("<br> ", "<br>").replace(" <br>", "<br>")
    print("TEXT:", text)
    
    placeholder = f"{{{{ t.key }}}}"
    for i, c in enumerate(nodes):
        if i == 0:
            # We want to replace it but with unescaped HTML?
            # Wait, NavigableString escapes HTML.
            # So if we put placeholder, that's fine.
            c.replace_with(placeholder)
        else:
            if isinstance(c, NavigableString):
                c.replace_with("")
            else:
                c.decompose()
        
print(soup.decode_contents())
