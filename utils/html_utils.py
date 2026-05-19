from html.parser import HTMLParser
from html import unescape
import re


class StructuredTextExtractor(HTMLParser):
    """
    Lightweight structured HTML extractor.

    Keeps only useful semantic tags and strips everything else while
    preserving readable structure for LLM consumption.
    """

    BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "blockquote",
    }

    HEADING_TAGS = {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    LIST_TAGS = {
        "ul",
        "ol",
        "li",
    }

    INLINE_TAGS = {
        "strong",
        "b",
        "em",
        "i",
        "code",
        "pre",
    }

    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "footer",
        "nav",
    }

    def __init__(self):
        super().__init__()

        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return

        if self.skip_depth:
            return

        # Headings
        if tag in self.HEADING_TAGS:
            level = int(tag[1])
            self.parts.append("\n" + ("#" * level) + " ")

        # Paragraph-like spacing
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")

        # Lists
        elif tag == "li":
            self.parts.append("\n- ")

        # Code blocks
        elif tag == "pre":
            self.parts.append("\n```text\n")

        elif tag == "code":
            self.parts.append("`")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return

        if self.skip_depth:
            return

        if tag == "pre":
            self.parts.append("\n```\n")

        elif tag == "code":
            self.parts.append("`")

        elif tag in self.BLOCK_TAGS or tag in self.HEADING_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return

        text = unescape(data)
        text = re.sub(r"\s+", " ", text).strip()

        if text:
            self.parts.append(text)

    def get_text(self):
        text = "".join(self.parts)

        # Cleanup excessive newlines
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()


def extract_text_from_html(html: str) -> str:
    parser = StructuredTextExtractor()
    parser.feed(html)
    return parser.get_text()