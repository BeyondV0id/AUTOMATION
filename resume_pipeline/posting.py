"""Turn a fetched posting into clean text and flag what should not be trusted.

Postings are third-party data. Two problems matter before any tailoring runs:

* the description is often HTML, so tags, CSS and script bodies leak into
  keyword scoring and into the optional model prompt;
* hidden markup can carry text the reader never sees, and invisible Unicode can
  smuggle instructions into a document that looks ordinary.

``plain_text`` returns only what a reader sees. ``guard_posting`` additionally
reports hidden blocks, invisible characters and instruction-shaped phrasing so
the caller can surface them. This is a deterministic gate, not a model: it
never rewrites the posting and never follows anything it finds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "br", "tr", "td", "th", "table",
    "section", "article", "header", "footer", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
INVISIBLE = (
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\ufeff",  # byte order mark
    "\u00ad",  # soft hyphen
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",  # bidi controls
    "\u2066", "\u2067", "\u2068", "\u2069",
)
INVISIBLE_TABLE = str.maketrans({char: None for char in INVISIBLE})

HIDDEN_STYLES = (
    re.compile(r"display\s*:\s*none\b"),
    re.compile(r"visibility\s*:\s*hidden\b"),
    re.compile(r"opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)"),
    re.compile(r"font-size\s*:\s*0(?:\.0+)?\s*(?:px|em|rem|%)?\s*(?:;|$)"),
    re.compile(r"color\s*:\s*transparent\b"),
    re.compile(r"(?:clip|clip-path)\s*:"),
    re.compile(r"(?:left|top|text-indent)\s*:\s*-\d{3,}px"),
)
ZERO_BOX = re.compile(r"(?:height|width)\s*:\s*0(?:\.0+)?(?:px|em|rem|%)?\s*(?:;|$)")
OVERFLOW_HIDDEN = re.compile(r"overflow\s*:\s*hidden\b")

INSTRUCTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)", re.I),
    re.compile(r"(?:system|developer)\s+prompt", re.I),
    re.compile(r"you\s+are\s+an?\s+(?:ai|assistant|language model)", re.I),
    re.compile(r"as\s+an?\s+ai\s+(?:language\s+)?model", re.I),
    re.compile(r"do\s+not\s+(?:tell|inform|reveal|mention)\s+(?:the\s+)?(?:user|candidate|applicant)", re.I),
    re.compile(r"instead,?\s+(?:send|wire|transfer|pay|email)\b", re.I),
    re.compile(r"(?:include|insert|copy)\s+the\s+following\s+(?:text|keyword|phrase)", re.I),
)


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove zero-width and bidi characters, returning the count removed."""
    found = sum(text.count(char) for char in INVISIBLE)
    return text.translate(INVISIBLE_TABLE), found


def _style_hidden(style: str) -> bool:
    value = re.sub(r"/\*.*?\*/", "", style or "", flags=re.S).lower()
    if any(pattern.search(value) for pattern in HIDDEN_STYLES):
        return True
    return bool(ZERO_BOX.search(value) and OVERFLOW_HIDDEN.search(value))


def _attrs_hidden(attributes: dict[str, str]) -> bool:
    if "hidden" in attributes:
        return True
    if attributes.get("aria-hidden", "").strip().lower() == "true":
        return True
    if attributes.get("type", "").strip().lower() == "hidden":
        return True
    return _style_hidden(attributes.get("style", ""))


class _PostingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.hidden_chunks: list[str] = []
        self._skip: list[str] = []
        self._hidden: list[bool] = []
        self._chunk: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip.append(tag)
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag in VOID_TAGS:
            if not any(self._hidden) and tag in BLOCK_TAGS:
                self.visible.append(" ")
            if _attrs_hidden(attributes):
                for key in ("value", "alt", "title", "placeholder", "content"):
                    if attributes.get(key):
                        self.hidden_chunks.append(" ".join(attributes[key].split())[:300])
            return
        parent_hidden = any(self._hidden)
        hidden = parent_hidden or _attrs_hidden(attributes)
        if not hidden and tag in BLOCK_TAGS:
            self.visible.append(" ")
        self._hidden.append(hidden)
        if hidden and not parent_hidden:
            self._chunk = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip:
            if tag == self._skip[-1]:
                self._skip.pop()
            return
        if tag in VOID_TAGS or not self._hidden:
            return
        was_hidden = self._hidden.pop()
        if not any(self._hidden) and tag in BLOCK_TAGS:
            self.visible.append(" ")
        if was_hidden and not any(self._hidden) and self._chunk is not None:
            text = " ".join(" ".join(self._chunk).split())
            if text:
                self.hidden_chunks.append(text[:300])
            self._chunk = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if any(self._hidden):
            if self._chunk is None:
                self._chunk = []
            self._chunk.append(data)
        else:
            self.visible.append(data)


def _parse(value: str) -> _PostingParser:
    parser = _PostingParser()
    parser.feed(value or "")
    parser.close()
    return parser


def plain_text(value: str) -> str:
    """Visible text only: tags, scripts, style blocks and hidden nodes removed."""
    text = value or ""
    if "<" in text and ">" in text:
        parser = _parse(text)
        text = "".join(parser.visible)
    else:
        text = unescape(text)
    text, _count = strip_invisible(text)
    return " ".join(text.split())


def instruction_flags(*texts: str) -> list[str]:
    """Snippets that look like instructions aimed at an automated reader."""
    flags: list[str] = []
    for text in texts:
        for pattern in INSTRUCTION_PATTERNS:
            for match in pattern.finditer(text or ""):
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 50)
                snippet = " ".join(text[start:end].split())
                if snippet not in flags:
                    flags.append(snippet)
    return flags


@dataclass
class Posting:
    text: str
    hidden_blocks: list[str] = field(default_factory=list)
    invisible: int = 0
    flags: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hidden_blocks and not self.flags

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "characters": len(self.text),
            "hidden_blocks": self.hidden_blocks,
            "invisible_characters": self.invisible,
            "flags": self.flags,
        }


def guard_posting(value: str) -> Posting:
    """Clean a posting and report hidden content, invisible text and flags."""
    raw = value or ""
    if "<" in raw and ">" in raw:
        parser = _parse(raw)
        visible = "".join(parser.visible)
        hidden_text = parser.hidden_chunks
    else:
        visible = unescape(raw)
        hidden_text = []
    visible, invisible = strip_invisible(visible)
    visible = " ".join(visible.split())

    cleaned_hidden = []
    for chunk in hidden_text:
        cleaned, _count = strip_invisible(chunk)
        if cleaned.strip():
            cleaned_hidden.append(" ".join(cleaned.split())[:300])

    flags = instruction_flags(visible, *cleaned_hidden)
    return Posting(text=visible, hidden_blocks=cleaned_hidden, invisible=invisible, flags=flags)
