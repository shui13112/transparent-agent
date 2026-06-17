"""Utility functions for web scraping.

This module provides helper functions for extracting text content
and processing HTML from web pages.
"""

import re

import bs4
from bs4 import BeautifulSoup


# Block-level elements that introduce visual line breaks in browser-rendered text.
_BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "tr", "section", "article", "header", "footer",
    "main", "aside", "ul", "ol", "dl", "table",
    "blockquote", "pre", "hr", "form", "fieldset",
    "figure", "figcaption", "details", "summary",
}

# Tags whose entire content (including children) is noise — removed before text extraction.
_BLACKLIST_TAGS = {
    "script", "style", "noscript", "iframe",
    "header", "footer", "nav", "aside",
    "button", "form", "svg", "canvas",
}

# CSS class substrings that signal non-content regions.
_BLACKLIST_CLASS_PATTERNS = [
    "nav", "menu", "sidebar", "footer", "header",
    "advertisement", "banner", "cookie", "popup",
    "modal", "social", "share", "comment",
    "related", "recommend", "breadcrumb", "pagination",
    "skip-link", "screen-reader",
]

# ARIA roles that signal non-content regions.
_BLACKLIST_ROLES = {
    "navigation", "banner", "contentinfo", "complementary",
    "search", "menu", "menubar", "form", "alert",
    "dialog", "tooltip",
}


def extract_title(soup: BeautifulSoup) -> str:
    """Extract the title from the BeautifulSoup object"""
    return soup.title.string if soup.title else ""


def _class_matches_blacklist(elem: bs4.Tag) -> bool:
    """Check if any CSS class on the element contains a blacklisted pattern."""
    classes = elem.get("class", [])
    if not classes:
        return False
    return any(
        any(pattern in cls.lower() for pattern in _BLACKLIST_CLASS_PATTERNS)
        for cls in classes
    )


def _role_matches_blacklist(elem: bs4.Tag) -> bool:
    """Check if the element's ARIA role is a non-content role."""
    role = elem.get("role")
    if not role:
        return False
    return any(r.lower() in _BLACKLIST_ROLES for r in role.split())


def clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove non-content nodes: blacklisted tags, nav/footer classes, and non-content ARIA roles."""
    # 1. Remove blacklisted tags and their contents
    for tag in soup.find_all(list(_BLACKLIST_TAGS)):
        tag.decompose()

    # 2. Remove elements whose CSS class marks them as non-content
    for tag in soup.find_all(_class_matches_blacklist):
        tag.decompose()

    # 3. Remove elements whose ARIA role marks them as non-content
    for tag in soup.find_all(_role_matches_blacklist):
        tag.decompose()

    return soup


def get_text_from_soup(soup: BeautifulSoup) -> str:
    """Extract text mimicking browser rendering: inline elements flow together,
    block elements introduce line breaks."""
    # Insert newlines around block elements for natural paragraph separation
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    # Replace <br> with newline
    for br in soup.find_all("br"):
        br.replace_with("\n")

    text = soup.get_text(strip=False, separator=" ")

    # Collapse whitespace: multiple spaces → one, spaces around newlines → removed
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


def parse_html(html: str | bytes) -> tuple[str, str]:
    """Parse raw HTML string into cleaned text and title."""
    soup = BeautifulSoup(html, "lxml")
    clean_soup(soup)
    title = extract_title(soup)
    text = get_text_from_soup(soup)
    return text, title