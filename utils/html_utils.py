"""HTML to markdown extraction utilities.

Uses beautifulsoup4 for robust parsing and content discovery,
html2text for faithful markdown conversion.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag
import html2text


def _find_main_content(soup: BeautifulSoup) -> Tag:
    """Locate the primary content region, falling back to <body>."""
    candidate = (
        soup.find("article")
        or soup.find("main")
        or soup.select_one("[role=main]")
    )
    if candidate and isinstance(candidate, Tag):
        return candidate
    if soup.body and isinstance(soup.body, Tag):
        return soup.body
    # Last resort: the whole doc wrapped in a synthetic div
    return soup


def _extract_title(soup: BeautifulSoup) -> str | None:
    """Return the page title if available."""
    tag = soup.find("title")
    if tag and tag.string:
        text = tag.get_text(strip=True)
        return text if text else None
    return None


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    """Remove elements that are not useful for content extraction."""
    for selector in (
        "script", "style", "noscript", "svg", "footer", "nav",
        "aside", "header", "form", "iframe", "canvas", "dialog",
        # Common class/id patterns for sidebars and ads
        "[class*=sidebar]", "[class*=side-bar]", "[id*=sidebar]",
        "[class*=ad-]", "[class*=advertisement]", "[id*=ad-]",
        "[class*=cookie]", "[id*=cookie]",
        "[class*=newsletter]", "[id*=newsletter]",
        "[class*=popup]", "[id*=popup]",
    ):
        for tag in soup.select(selector):
            tag.decompose()


def _minimize_tokens(markdown: str) -> str:
    """Compress whitespace to reduce token count without losing structure."""
    # Collapse 3+ consecutive blank lines down to 2
    text = re.sub(r"\n{4,}", "\n\n\n", markdown)
    # Strip trailing whitespace per line (safe for markdown tables)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def _build_markdown_converter() -> html2text.HTML2Text:
    """Configure html2text for compact, faithful markdown output."""
    conv = html2text.HTML2Text()
    conv.body_width = 0                # Prevent line wrapping
    conv.ignore_links = False
    conv.ignore_images = False
    conv.ignore_emphasis = False
    conv.ignore_tables = False
    conv.protect_links = False         # [text](url) not [text](<url>)
    conv.backquote_code_style = "```"  # Proper fenced code blocks
    conv.unicode_snob = True           # Use Unicode, not HTML entities
    conv.single_line_break = False     # \n\n between blocks for proper markdown spacing
    conv.escape_snob = False
    conv.pad_tables = True
    return conv


def extract_text_from_html(html: str) -> str:
    """
    Extract meaningful content from HTML and return as clean markdown.

    * Removes boilerplate (nav, footer, sidebars, scripts, ads, forms, …)
    * Focuses on the main content region (``<article>`` / ``<main>`` / ``[role=main]``)
    * Preserves links, images, tables, code blocks (with language hints)
    * Outputs compact markdown minimising token count.
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # 1. Strip unwanted elements
    _strip_boilerplate(soup)

    # 2. Extract metadata
    title = _extract_title(soup)

    # 3. Find main content region
    content = _find_main_content(soup)

    # 4. Serialise the cleaned content to HTML
    content_html = str(content)

    # Prepend title as H1 if it isn't already in the extracted region
    if title and title not in content_html:
        content_html = f"<h1>{title}</h1>\n\n{content_html}"

    # 5. Convert to markdown
    converter = _build_markdown_converter()
    markdown = converter.handle(content_html)

    # 6. Token-minimising post-processing
    return _minimize_tokens(markdown)
