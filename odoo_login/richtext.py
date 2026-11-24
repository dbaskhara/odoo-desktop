"""Turns Odoo's HTML (task descriptions, chatter messages) into simple text blocks."""

from html.parser import HTMLParser

BLOCK_TAGS = {"p", "div", "section", "article", "blockquote", "table", "tr", "ul", "ol"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP = {"style", "script", "head", "title"}


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []  # (kind, text) with kind in p | h | li | check | done | pre
        self._kind = "p"
        self._text = []
        self._skip = 0
        self._lists = []  # stack of ["ul"|"ol"|"check", counter]
        self._pre = 0

    def _flush(self, next_kind="p"):
        text = "".join(self._text)
        if self._kind != "pre":
            text = " ".join(text.split())
        text = text.strip("\n") if self._kind == "pre" else text.strip()
        if text:
            self.blocks.append((self._kind, text))
        self._text = []
        self._kind = next_kind

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in SKIP:
            self._skip += 1
        elif tag in HEADINGS:
            self._flush("h")
        elif tag == "pre":
            self._flush("pre")
            self._pre += 1
        elif tag in ("ul", "ol"):
            self._flush()
            checklist = "o_checklist" in (attrs.get("class") or "")
            self._lists.append(["check" if checklist else tag, 0])
        elif tag == "li":
            kind = "li"
            if self._lists and self._lists[-1][0] == "check":
                kind = "done" if "o_checked" in (attrs.get("class") or "") else "check"
            numbered = bool(self._lists) and self._lists[-1][0] == "ol"
            self._flush("ol" if numbered else kind)
            if numbered:
                self._lists[-1][1] += 1
                self._text.append(f"{self._lists[-1][1]}. ")
        elif tag == "br":
            self._text.append("\n" if self._pre else " ")
            if not self._pre:
                self._flush(self._kind if self._kind in ("li", "ol", "check", "done") else "p")
        elif tag == "img":
            self._text.append("[image]")
        elif tag in ("td", "th"):
            self._text.append("   ")
        elif tag in BLOCK_TAGS:
            self._flush(self._kind if self._kind in ("li", "ol", "check", "done") and tag in ("p", "div") else "p")

    def handle_endtag(self, tag):
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "pre":
            self._flush()
            self._pre = max(0, self._pre - 1)
        elif tag in ("ul", "ol"):
            self._flush()
            if self._lists:
                self._lists.pop()
        elif tag in HEADINGS or tag == "li" or tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)


def html_to_blocks(html):
    """List of (kind, text). kind: p, h, li, ol (numbered), check, done (ticked checklist item), pre."""
    if not html or not isinstance(html, str):
        return []
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return [("p", " ".join(html.split()))]
    parser._flush()
    return parser.blocks


def html_to_text(html):
    lines = []
    for kind, text in html_to_blocks(html):
        prefix = {"li": "• ", "check": "☐ ", "done": "☑ "}.get(kind, "")
        lines.append(prefix + text)
    return "\n".join(lines)
