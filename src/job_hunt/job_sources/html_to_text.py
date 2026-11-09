import html
import re
from html.parser import HTMLParser

# tags that start a new line when an advert is read as plain text
BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}
)
SKIPPED_TAGS = frozenset({"script", "style"})


class _PlainTextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


# Adverts arrive as HTML (sometimes escaped twice, as on Greenhouse); the
# filtering step later reads plain text.
def html_to_text(raw_html: str | None, is_escaped: bool = False) -> str | None:
    if not raw_html:
        return None
    collector = _PlainTextCollector()
    collector.feed(html.unescape(raw_html) if is_escaped else raw_html)
    collector.close()
    text = "".join(collector.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None
