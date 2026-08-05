import re
from bs4 import BeautifulSoup, NavigableString

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
    direct_texts = [
        c for c in el.contents
        if isinstance(c, NavigableString)
    ]
    text = "".join(str(c) for c in direct_texts).strip()
    text = re.sub(r"\s+", " ", text).strip()
    
    placeholder = f"{{{{ t.key }}}}"
    for c in direct_texts:
        c.replace_with(placeholder if c is direct_texts[0] else "")
        
print(soup.decode_contents())
