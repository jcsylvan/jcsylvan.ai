#!/usr/bin/env python3
"""Export Control Library generator for jcsylvan.ai.

The library is a set of daily export control briefs. Each brief is kept as one
plain text file in export-control/_briefs/ (a short header plus the memo text in
a Markdown subset). This script turns those files into the public pages:

    export-control/index.html          the library index
    export-control/<date>-<slug>.html  one page per brief
    export-control/library.css         the shared stylesheet
    export-control/library.json        a machine-readable list of what is published

Commands (run from the repository root):

    python3 tools/build_site.py build
        Regenerate every page from the source files.

    python3 tools/build_site.py add FILE [FILE ...]
        Add the brief or briefs in FILE to the sources, then build. A file may
        hold several briefs, each introduced by a line that reads
        "===== BRIEF =====".

    python3 tools/build_site.py check
        Validate the sources and confirm the pages on disk match them.

    python3 tools/build_site.py fingerprint FILE
        Print the fingerprint of the brief in FILE.

    python3 tools/build_site.py from-docx MEMO.docx [INDEX.docx]
        Print a Word memo as brief text (needs pandoc).

    python3 tools/build_site.py DOCX_FOLDER OUTPUT_FOLDER
        Import every "YYYY-MM-DD Export Control Brief - *.docx" in DOCX_FOLDER,
        using the index in the same folder for the header fields, and build
        into OUTPUT_FOLDER (needs pandoc).

Apart from pandoc for the two Word commands, this uses the Python standard
library only. The pages are generated: do not edit them by hand. Edit the
source file and run "build".
"""

import hashlib
import html
import json
import re
import subprocess
import sys
from datetime import date as _date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "export-control"
SRC_DIRNAME = "_briefs"

SITE = "https://jcsylvan.ai"
SECTION_URL = SITE + "/export-control/"
BRIEF_MARK = "===== BRIEF ====="
INDEX_DOCX = "Export Control Library Index.docx"
DOCX_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) Export Control Brief - (.+?)(\.docx)?$")

THEMES = {
    "A": "ITAR foundations",
    "B": "USML categories",
    "C": "ITAR licensing and agreements",
    "D": "EAR scope and classification",
    "E": "EAR licensing",
    "F": "OFAC and sanctions",
    "G": "Enforcement and disclosure",
    "H": "Intersections with cyber and government contracts",
    "I": "Allied and multilateral regimes",
}

REQUIRED = ("date", "title", "theme", "topic", "rule")
FIELD_ORDER = ("date", "title", "theme", "topic", "instruments", "rule", "docx")

# The memos frame their hypotheticals around an unnamed company. On the public
# pages that company is called "the hypothetical prime", so that no reader takes
# a study scenario for a description of a real business.
GENERIC_SUBS = (
    (re.compile(r"\bThe [Cc]ompany\b"), "The hypothetical prime"),
    (re.compile(r"\bthe [Cc]ompany\b"), "the hypothetical prime"),
)

DISCLAIMER = "Regulatory analysis, not legal advice."
NOTE = (
    "Personal study notes. Each page is a memo to file prepared on the date shown. The scenarios in "
    "the Question sections are hypotheticals written for study and do not describe any employer, client, "
    "or matter. Citations were checked against the primary source on that date and may have changed "
    "since. Verify every instrument before relying on it."
)

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


class BriefError(Exception):
    """A problem with a brief that should stop the run."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def esc(text, quote=False):
    return html.escape(text, quote=quote)


def long_date(iso):
    y, m, d = (int(p) for p in iso.split("-"))
    return "%s %d, %d" % (MONTHS[m - 1], d, y)


def short_date(iso):
    y, m, d = (int(p) for p in iso.split("-"))
    return "%s %d, %d" % (MONTHS[m - 1][:3], d, y)


def slugify(text, limit=60):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].rsplit("-", 1)[0] or slug[:limit]
    return slug or "brief"


_FOLD = (("\u2018", "'"), ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
         ("\u2013", "-"), ("\u2014", "-"), ("\u00a0", " "), ("\\", ""))


def normalise(text):
    """The text a fingerprint is taken over.

    The fingerprint line itself is left out. Curly quotes, long dashes,
    non-breaking spaces, backslashes and runs of white space are folded, so
    that only a change in the words can change the fingerprint.
    """
    text = "\n".join(l for l in text.splitlines() if not l.startswith("fingerprint:"))
    for a, b in _FOLD:
        text = text.replace(a, b)
    return " ".join(text.split())


def fingerprint(text):
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Brief files: header plus body
# --------------------------------------------------------------------------

class Brief:
    def __init__(self, meta, body):
        self.meta = meta
        self.body = body.strip("\n") + "\n"

    @property
    def slug(self):
        if self.meta.get("slug"):
            return slugify(self.meta["slug"])
        m = DOCX_NAME_RE.match(self.meta.get("docx", ""))
        if m:
            return slugify(m.group(2))
        return slugify(self.heading)

    @property
    def stem(self):
        return "%s-%s" % (self.meta["date"], self.slug)

    @property
    def page(self):
        return self.stem + ".html"

    @property
    def heading(self):
        """The topic part of the title, used as the page heading."""
        title = self.meta.get("title", "")
        m = re.match(r"Export Control Brief for [^:]+:\s*(.+)$", title)
        return m.group(1) if m else (title or self.meta.get("topic", ""))

    def text(self):
        lines = ["---"]
        for key in FIELD_ORDER:
            if self.meta.get(key):
                lines.append("%s: %s" % (key, self.meta[key]))
        lines.append("slug: %s" % self.slug)
        lines.append("---")
        return "\n".join(lines) + "\n\n" + self.body


def parse_brief(text, where="brief"):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise BriefError("%s: the brief must start with a header between two '---' lines" % where)
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise BriefError("%s: the header is not closed with a '---' line" % where)
    meta = {}
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if ":" not in raw:
            raise BriefError("%s: header line is not 'key: value': %r" % (where, raw[:80]))
        key, value = raw.split(":", 1)
        value = " ".join(value.split())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        meta[key.strip().lower()] = value
    theme = meta.get("theme", "")
    if re.match(r"^[A-Ia-i]\b", theme):
        meta["theme"] = theme[0].upper()
    return Brief(meta, "\n".join(lines[end + 1:]))


def split_payload(text):
    """A payload holds one brief, or several introduced by the BRIEF_MARK line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts, current = [], []
    for line in text.split("\n"):
        if line.strip() == BRIEF_MARK:
            if "".join(current).strip():
                parts.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if "".join(current).strip():
        parts.append("\n".join(current))
    return parts


def validate(brief, where):
    problems = []
    meta = brief.meta
    for key in REQUIRED:
        if not meta.get(key):
            problems.append("missing '%s' in the header" % key)
    d = meta.get("date", "")
    if d:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                raise ValueError
            _date.fromisoformat(d)
        except ValueError:
            problems.append("date must be YYYY-MM-DD, got %r" % d)
    theme = meta.get("theme", "")
    if theme and theme not in THEMES:
        problems.append("theme must be one letter A to I, got %r" % theme)
    if not brief.body.strip():
        problems.append("the brief has no text")
    for m in re.finditer(r"<\s*(script|iframe|style|object|embed|form)\b", brief.body, re.I):
        problems.append("the text contains an HTML <%s> tag" % m.group(1).lower())
    if problems:
        raise BriefError("%s: %s" % (where, "; ".join(problems)))


def apply_generic_subs(text):
    for pattern, repl in GENERIC_SUBS:
        text = pattern.sub(repl, text)
    return text


def load_sources(src_dir):
    briefs = []
    if not src_dir.is_dir():
        return briefs
    for path in sorted(src_dir.glob("*.md")):
        brief = parse_brief(path.read_text(encoding="utf-8"), path.name)
        validate(brief, path.name)
        if path.stem != brief.stem:
            raise BriefError("%s: file name does not match its date and slug (expected %s.md)"
                             % (path.name, brief.stem))
        briefs.append(brief)
    briefs.sort(key=lambda b: (b.meta["date"], b.slug), reverse=True)
    return briefs


# --------------------------------------------------------------------------
# Markdown subset to HTML
#
# The subset covers what the memos use and what pandoc writes for them:
# headings, paragraphs, bold and italic, links, bare and <angle> URLs, bullet
# and numbered lists, pipe tables, block quotes, and endnotes written either
# as "[1] text" paragraphs under an Endnotes heading or as [^1] definitions.
# Everything else is shown as plain text. No raw HTML passes through.
# --------------------------------------------------------------------------

URL_RE = re.compile(r"https?://[^\s<>\"]+")
AUTOLINK_RE = re.compile(r"<(https?://[^\s<>]+)>")
SUPSUB_RE = re.compile(r"<(sup|sub)>([^<>]{1,40})</\1>")
NOTE_REF_RE = re.compile(r"\[\^([A-Za-z0-9]+)\]")
PLAIN_REF_RE = re.compile(r"\[(\d{1,3})\](?!\()")
ESC_REF_RE = re.compile(r"\\\[(\d{1,3})\\\]")
NOTE_PARA_RE = re.compile(r"^\\?\[(\d{1,3})\\?\]\s+(.+)$", re.S)
SAFE_URL_RE = re.compile(r"^(https?://|mailto:)[^\s\"<>]+$|^#[A-Za-z0-9._-]*$|^[A-Za-z0-9._/-]+\.html(#[A-Za-z0-9._-]*)?$")
ESCAPABLE = set("\\`*_{}[]()#+-.!<>|~$'\"&:=^@")


def _match_close(text, start, open_ch, close_ch):
    depth, i = 0, start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _unescape(text):
    return re.sub(r"\\(.)", lambda m: m.group(1) if m.group(1) in ESCAPABLE else m.group(0), text)


def _trim_url(url):
    while url:
        last = url[-1]
        if last in ".,;:!?'\"*\\":
            url = url[:-1]
        elif last == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        elif last == "]" and url.count("]") > url.count("["):
            url = url[:-1]
        else:
            break
    return url


class Page:
    """Per-page state for endnotes."""

    def __init__(self, note_ids=()):
        self.note_ids = set(note_ids)
        self.seen_refs = set()

    def note_ref(self, ident):
        first = ident not in self.seen_refs
        self.seen_refs.add(ident)
        anchor = ' id="enref-%s"' % esc(ident, True) if first else ""
        return '<a class="en" href="#en-%s"%s>[%s]</a>' % (esc(ident, True), anchor, esc(ident))


def link_html(url, label_html):
    rel = ' rel="noopener"' if url.startswith("http") else ""
    return '<a href="%s"%s>%s</a>' % (esc(url, True), rel, label_html)


def render_inline(text, page, links=True):
    frags = []

    def stash(fragment):
        frags.append(fragment)
        return "\x00%d\x00" % (len(frags) - 1)

    buf = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            m = ESC_REF_RE.match(text, i)
            if m and links and m.group(1) in page.note_ids:
                buf.append(stash(page.note_ref(m.group(1))))
                i = m.end()
                continue
            if i + 1 < n and text[i + 1] in ESCAPABLE:
                buf.append(stash(esc(text[i + 1])))
                i += 2
                continue
        elif ch == "`":
            j = text.find("`", i + 1)
            if j > i:
                buf.append(stash("<code>%s</code>" % esc(text[i + 1:j])))
                i = j + 1
                continue
        elif ch == "<":
            m = AUTOLINK_RE.match(text, i)
            if m:
                url = m.group(1)
                buf.append(stash(link_html(url, esc(url)) if links else esc(url)))
                i = m.end()
                continue
            m = SUPSUB_RE.match(text, i)
            if m:
                buf.append(stash("<%s>%s</%s>" % (m.group(1), esc(m.group(2)), m.group(1))))
                i = m.end()
                continue
        elif ch == "[":
            m = NOTE_REF_RE.match(text, i)
            if m and links:
                buf.append(stash(page.note_ref(m.group(1))))
                i = m.end()
                continue
            m = PLAIN_REF_RE.match(text, i)
            if m and links and m.group(1) in page.note_ids:
                buf.append(stash(page.note_ref(m.group(1))))
                i = m.end()
                continue
            close = _match_close(text, i, "[", "]")
            if close != -1 and close + 1 < n and text[close + 1] == "(":
                end = _match_close(text, close + 1, "(", ")")
                if end != -1:
                    url = _unescape(text[close + 2:end].strip())
                    if SAFE_URL_RE.match(url):
                        label = render_inline(text[i + 1:close], page, links=False)
                        buf.append(stash(link_html(url, label) if links else label))
                        i = end + 1
                        continue
        elif ch == "h" and links:
            m = URL_RE.match(text, i)
            if m and (i == 0 or not text[i - 1].isalnum()):
                raw = _trim_url(m.group(0))
                if len(raw) > 10:
                    url = _unescape(raw)
                    buf.append(stash(link_html(url, esc(url))))
                    i += len(raw)
                    continue
        buf.append(ch)
        i += 1

    out = esc("".join(buf))
    out = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<em>\1</em>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: frags[int(m.group(1))], out)


HEADING_RE = re.compile(r"^(#{1,5})\s+(.*?)\s*#*\s*$")
BULLET_RE = re.compile(r"^(\s*)[-*\u2022]\s+(.*)$")
ORDERED_RE = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
NOTE_DEF_RE = re.compile(r"^\[\^([A-Za-z0-9]+)\]:\s*(.*)$")
RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _starts_block(line):
    return bool(HEADING_RE.match(line) or BULLET_RE.match(line) or ORDERED_RE.match(line)
                or NOTE_DEF_RE.match(line) or RULE_RE.match(line)
                or line.lstrip().startswith(("|", ">")))


def parse_blocks(body):
    """Turn the body text into a list of block tuples."""
    lines = body.replace("\t", "    ").split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if RULE_RE.match(line):
            blocks.append(("hr",))
            i += 1
            continue
        m = HEADING_RE.match(line)
        if m:
            blocks.append(("h", len(m.group(1)), m.group(2)))
            i += 1
            continue
        m = NOTE_DEF_RE.match(line)
        if m:
            ident, parts = m.group(1), [m.group(2)]
            i += 1
            while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
                parts.append(lines[i].strip())
                i += 1
            blocks.append(("note", ident, " ".join(p for p in parts if p)))
            continue
        if line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            blocks.append(("table", rows))
            continue
        if line.lstrip().startswith(">"):
            parts = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                parts.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append(("quote", " ".join(p for p in parts if p)))
            continue
        if BULLET_RE.match(line) or ORDERED_RE.match(line):
            items = []
            while i < len(lines):
                cur = lines[i]
                mb, mo = BULLET_RE.match(cur), ORDERED_RE.match(cur)
                if mb:
                    items.append([len(mb.group(1)), False, None, mb.group(2)])
                elif mo:
                    items.append([len(mo.group(1)), True, int(mo.group(2)), mo.group(3)])
                elif cur.strip() and cur.startswith(" ") and items and not _starts_block(cur):
                    items[-1][3] += " " + cur.strip()
                elif (not cur.strip() and i + 1 < len(lines)
                      and (BULLET_RE.match(lines[i + 1]) or ORDERED_RE.match(lines[i + 1]))):
                    pass
                else:
                    break
                i += 1
            blocks.append(("list", items))
            continue
        parts = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
            parts.append(lines[i].strip())
            i += 1
        blocks.append(("p", " ".join(parts)))
    return blocks


def _settle_headings(blocks):
    """Make the memo's top sections level 2, whichever way the text marks them.

    A single '#' line at the very top is the title, which the page already
    shows. Several '#' headings (what pandoc writes for Word's Heading 1) are
    the memo's sections, so every heading moves down one level.
    """
    tops = [b for b in blocks if b[0] == "h" and b[1] == 1]
    if not tops:
        return blocks
    if len(tops) == 1 and blocks[0][0] == "h" and blocks[0][1] == 1:
        return blocks[1:]
    return [("h", b[1] + 1, b[2]) if b[0] == "h" else b for b in blocks]


def _settle_endnotes(blocks):
    """Under an Endnotes heading, read '[1] text' paragraphs and plain numbered lists as endnotes."""
    out, in_notes = [], False
    for block in blocks:
        kind = block[0]
        if kind == "h":
            in_notes = bool(re.match(r"(end)?notes\b", _unescape(block[2]).strip(), re.I))
            out.append(block)
        elif in_notes and kind == "p" and NOTE_PARA_RE.match(block[1]):
            m = NOTE_PARA_RE.match(block[1])
            out.append(("note", m.group(1), m.group(2)))
        elif in_notes and kind == "list" and all(it[1] and it[0] == block[1][0][0] for it in block[1]):
            for it in block[1]:
                out.append(("note", str(it[2]), it[3]))
        else:
            out.append(block)
    return out


def _render_list(items, page):
    """Render a flat list of [indent, ordered, number, text] as nested HTML."""
    def build(pos, indent):
        ordered = items[pos][1]
        tag = "ol" if ordered else "ul"
        start = ""
        if ordered and items[pos][2] not in (None, 1):
            start = ' start="%d"' % items[pos][2]
        parts = ["<%s%s>" % (tag, start)]
        while pos < len(items) and items[pos][0] >= indent:
            if items[pos][0] > indent:
                inner, pos = build(pos, items[pos][0])
                if parts[-1].endswith("</li>"):
                    parts[-1] = parts[-1][:-5] + inner + "</li>"
                else:
                    parts.append("<li>%s</li>" % inner)
                continue
            parts.append("<li>%s</li>" % render_inline(items[pos][3], page))
            pos += 1
        parts.append("</%s>" % tag)
        return "\n".join(parts), pos

    html_out, pos = [], 0
    while pos < len(items):
        chunk, pos = build(pos, items[pos][0])
        html_out.append(chunk)
    return "\n".join(html_out)


def _split_row(row):
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def _render_table(rows, page):
    has_head = len(rows) > 1 and TABLE_SEP_RE.match(rows[1])
    out = ['<div class="table-wrap"><table>']
    body_rows = rows
    if has_head:
        cells = _split_row(rows[0])
        out.append("<thead><tr>%s</tr></thead>"
                   % "".join("<th>%s</th>" % render_inline(c, page) for c in cells))
        body_rows = rows[2:]
    out.append("<tbody>")
    for row in body_rows:
        if TABLE_SEP_RE.match(row):
            continue
        out.append("<tr>%s</tr>" % "".join("<td>%s</td>" % render_inline(c, page) for c in _split_row(row)))
    out.append("</tbody></table></div>")
    return "\n".join(out)


def render_body(body):
    blocks = _settle_endnotes(_settle_headings(parse_blocks(body)))
    page = Page(note_ids=[b[1] for b in blocks if b[0] == "note"])
    out = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        kind = block[0]
        if kind == "h":
            level, text = block[1], block[2]
            out.append('<h%d id="%s">%s</h%d>'
                       % (level, slugify(_unescape(text), 50), render_inline(text, page), level))
        elif kind == "p":
            text = block[1]
            if re.fullmatch(r"[*_]{0,2}Regulatory analysis, not legal advice\.?[*_]{0,2}", text.strip()):
                out.append('<p class="closing">%s</p>' % esc(DISCLAIMER))
            elif i == 0 and text.startswith("Memo to file."):
                out.append('<p class="prepared">%s</p>' % render_inline(text, page))
            else:
                out.append("<p>%s</p>" % render_inline(text, page))
        elif kind == "list":
            out.append(_render_list(block[1], page))
        elif kind == "quote":
            out.append("<blockquote><p>%s</p></blockquote>" % render_inline(block[1], page))
        elif kind == "table":
            out.append(_render_table(block[1], page))
        elif kind == "hr":
            out.append("<hr>")
        elif kind == "note":
            notes = []
            while i < len(blocks) and blocks[i][0] == "note":
                notes.append(blocks[i])
                i += 1
            out.append('<ol class="endnotes">')
            for _, ident, text in notes:
                back = ""
                if ident in page.seen_refs:
                    back = (' <a class="back" href="#enref-%s" aria-label="Back to the text">&#8617;</a>'
                            % esc(ident, True))
                out.append('<li id="en-%s"><span class="n">[%s]</span> %s%s</li>'
                           % (esc(ident, True), esc(ident), render_inline(text, page), back))
            out.append("</ol>")
            continue
        i += 1
    return "\n".join(out), page


# --------------------------------------------------------------------------
# Page templates
# --------------------------------------------------------------------------

CSS = """\
/* Export Control Library. Generated by tools/build_site.py. Do not edit by hand. */
* { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --bg: #0a0a0a;
  --panel: #111;
  --line: #222;
  --fg: #e0e0e0;
  --text: #d2d2d2;
  --muted: #888;
  --dim: #666;
  --accent: #f0c040;
  --mono: "SF Mono", "Fira Code", "Fira Mono", "Roboto Mono", monospace;
  --serif: Georgia, "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
}

body {
  font-family: var(--mono);
  background-color: var(--bg);
  color: var(--fg);
  min-height: 100vh;
  padding: 2rem;
  line-height: 1.5;
  -webkit-text-size-adjust: 100%;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.wrap { max-width: 820px; margin: 0 auto; }

.back-link {
  display: inline-block;
  margin-bottom: 1.5rem;
  color: var(--dim);
  font-size: 0.85rem;
}
.back-link:hover { color: var(--fg); }

h1 {
  font-size: 1.4rem;
  line-height: 1.35;
  margin-bottom: 0.35rem;
  color: var(--accent);
  font-weight: 600;
}

.subtitle {
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.6;
  margin-bottom: 1.5rem;
}

/* ---------- index ---------- */

.toolbar { margin-bottom: 0.75rem; }

.search {
  width: 100%;
  background: var(--panel);
  border: 1px solid #333;
  border-radius: 6px;
  color: var(--fg);
  font-family: inherit;
  font-size: 0.85rem;
  padding: 0.55rem 0.75rem;
}
.search:focus { outline: none; border-color: var(--accent); }
.search::placeholder { color: #555; }

.chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 1rem; }

.chip {
  background: none;
  border: 1px solid #333;
  border-radius: 999px;
  color: var(--muted);
  font-family: inherit;
  font-size: 0.75rem;
  padding: 0.3rem 0.75rem;
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease, background 0.15s ease;
}
.chip:hover { color: var(--fg); border-color: var(--muted); }
.chip.active { color: var(--bg); background: var(--accent); border-color: var(--accent); }
.chip:disabled { color: #444; border-color: #1c1c1c; cursor: default; }

.stats { color: var(--dim); font-size: 0.75rem; margin-bottom: 0.75rem; }

.briefs { display: grid; gap: 0.75rem; }

.brief {
  display: block;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 1rem 1.15rem;
  color: inherit;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.brief:hover { border-color: var(--accent); background: #151515; text-decoration: none; }
.brief[hidden] { display: none; }

.brief .when { display: block; color: var(--dim); font-size: 0.75rem; letter-spacing: 0.03em; }
.brief .when .theme { color: var(--muted); }
.brief .topic {
  display: block;
  color: var(--accent);
  font-size: 0.98rem;
  font-weight: 600;
  line-height: 1.4;
  margin: 0.3rem 0 0.45rem;
}
.brief .topic .arrow { color: #555; font-size: 0.85rem; font-weight: 400; }
.brief:hover .topic .arrow { color: var(--accent); }
.brief .rule, .brief .instruments {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.brief .rule { color: #aaa; font-size: 0.8rem; line-height: 1.55; -webkit-line-clamp: 4; line-clamp: 4; }
.brief .instruments {
  color: var(--dim);
  font-size: 0.74rem;
  line-height: 1.5;
  margin-top: 0.5rem;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}

.empty {
  border: 1px dashed #333;
  border-radius: 6px;
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.6;
  padding: 1.25rem;
}
.empty[hidden] { display: none; }

.coverage { margin-top: 3rem; }
.coverage h2 { color: var(--accent); font-size: 1.05rem; font-weight: 600; margin-bottom: 0.35rem; }
.coverage .note { color: var(--muted); font-size: 0.82rem; line-height: 1.6; margin-bottom: 1.25rem; }

.theme-block { border-top: 1px solid var(--line); padding: 0.9rem 0; }
.theme-head { display: flex; align-items: baseline; gap: 0.6rem; font-size: 0.88rem; color: var(--fg); }
.theme-head .letter { color: var(--accent); font-weight: 600; }
.theme-head .n { margin-left: auto; color: var(--dim); font-size: 0.75rem; white-space: nowrap; }
.theme-block ul { list-style: none; margin-top: 0.5rem; }
.theme-block li { color: var(--muted); font-size: 0.82rem; line-height: 1.5; padding: 0.15rem 0 0.15rem 1.35rem; }
.theme-block li .d { color: var(--dim); font-size: 0.75rem; white-space: nowrap; }
.theme-block.none .theme-head { color: var(--dim); }
.theme-block.none .letter { color: #555; }

/* ---------- one brief ---------- */

.kicker { color: var(--muted); font-size: 0.78rem; letter-spacing: 0.03em; margin-bottom: 0.5rem; }

.facts {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  color: #aaa;
  font-size: 0.8rem;
  line-height: 1.6;
  margin: 1.1rem 0 1.75rem;
  padding: 0.9rem 1rem;
  overflow-wrap: anywhere;
}
.facts > div + div, .facts details { margin-top: 0.5rem; }
.facts .label { color: var(--dim); }
.facts summary { color: var(--muted); cursor: pointer; }
.facts summary:hover { color: var(--accent); }
.facts details p { margin-top: 0.5rem; }

.memo { color: var(--text); font-family: var(--serif); font-size: 1.06rem; line-height: 1.7; }
.memo h2 {
  border-top: 1px solid var(--line);
  color: var(--accent);
  font-family: var(--mono);
  font-size: 1.05rem;
  font-weight: 600;
  line-height: 1.4;
  margin-top: 2.25rem;
  padding-top: 1.5rem;
  margin-bottom: 0.9rem;
}
.memo h3 {
  color: var(--fg);
  font-family: var(--mono);
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin: 1.75rem 0 0.6rem;
}
.memo h4, .memo h5 { color: var(--fg); font-family: var(--mono); font-size: 0.85rem; font-weight: 600; margin: 1.4rem 0 0.5rem; }
.memo p { margin-bottom: 1.05rem; }
.memo .prepared { color: var(--muted); font-family: var(--mono); font-size: 0.78rem; line-height: 1.6; }
.memo ul, .memo ol { margin: 0 0 1.05rem 1.6rem; }
.memo li { margin-bottom: 0.5rem; }
.memo li > ul, .memo li > ol { margin-top: 0.5rem; margin-bottom: 0; }
.memo strong { color: #f2f2f2; font-weight: 700; }
.memo a { overflow-wrap: anywhere; }
.memo a.en { font-family: var(--mono); font-size: 0.72em; }
.memo code { background: #161616; border: 1px solid var(--line); border-radius: 3px; font-family: var(--mono); font-size: 0.8em; padding: 0.05em 0.3em; }
.memo blockquote { border-left: 2px solid #3a3320; color: #aaa; margin: 0 0 1.05rem; padding-left: 1rem; }
.memo hr { border: 0; border-top: 1px solid var(--line); margin: 2rem 0; }

.table-wrap { overflow-x: auto; margin-bottom: 1.25rem; border: 1px solid var(--line); border-radius: 6px; }
.memo table { border-collapse: collapse; font-size: 0.9rem; line-height: 1.55; width: 100%; }
.memo th, .memo td { border-bottom: 1px solid var(--line); padding: 0.55rem 0.7rem; text-align: left; vertical-align: top; }
.memo th { background: var(--panel); color: var(--fg); font-family: var(--mono); font-size: 0.75rem; font-weight: 600; }
.memo tr:last-child td { border-bottom: 0; }

.memo ol.endnotes { color: #a8a8a8; font-size: 0.9rem; line-height: 1.6; list-style: none; margin-left: 0; }
.memo ol.endnotes li { margin-bottom: 0.7rem; overflow-wrap: anywhere; padding-left: 2.9em; position: relative; }
.memo ol.endnotes .n { color: var(--dim); font-family: var(--mono); font-size: 0.8em; left: 0; position: absolute; top: 0.15em; }
.memo ol.endnotes a { font-size: 0.9em; }
.memo ol.endnotes li:target { color: var(--fg); }
.memo ol.endnotes .back { color: var(--dim); font-family: var(--mono); }
.memo .closing { color: var(--accent); font-family: var(--mono); font-size: 0.8rem; margin-top: 2rem; }

.pager {
  border-top: 1px solid var(--line);
  display: flex;
  justify-content: space-between;
  gap: 1.5rem;
  font-size: 0.8rem;
  line-height: 1.5;
  margin-top: 2.5rem;
  padding-top: 1.25rem;
}
.pager a { color: var(--muted); max-width: 48%; }
.pager a:hover { color: var(--accent); text-decoration: none; }
.pager .dir { display: block; color: var(--dim); font-size: 0.72rem; letter-spacing: 0.05em; margin-bottom: 0.2rem; }
.pager .next { margin-left: auto; text-align: right; }

.foot {
  border-top: 1px solid var(--line);
  color: var(--dim);
  font-size: 0.75rem;
  line-height: 1.6;
  margin-top: 2.5rem;
  padding-top: 1.25rem;
}
.foot .disc { color: var(--muted); margin-bottom: 0.4rem; }

@media (max-width: 600px) {
  body { padding: 1.1rem; }
  h1 { font-size: 1.2rem; }
  .memo { font-size: 1rem; line-height: 1.65; }
  .pager { flex-direction: column; }
  .pager a { max-width: none; }
  .pager .next { margin-left: 0; text-align: left; }
}

@media print {
  body { background: #fff; color: #000; padding: 0; }
  .back-link, .pager, .toolbar, .chips, .stats { display: none; }
  h1, .memo h2, .memo h3, .memo h4, .memo strong, .coverage h2, .memo .closing { color: #000; }
  .memo, .kicker, .facts, .subtitle, .foot, .foot .disc, .memo ol.endnotes, .memo .prepared { color: #222; }
  .facts, .table-wrap, .brief { border-color: #999; background: none; }
  a { color: #000; text-decoration: underline; }
}
"""

INDEX_SCRIPT = """\
(function () {
  var q = document.getElementById('q');
  if (!q) return;
  var items = [].slice.call(document.querySelectorAll('.brief'));
  var chips = [].slice.call(document.querySelectorAll('.chip'));
  var stats = document.getElementById('stats');
  var none = document.getElementById('none');
  var theme = '';
  function apply() {
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
    var shown = 0;
    items.forEach(function (el) {
      var text = el.getAttribute('data-text');
      var ok = (!theme || el.getAttribute('data-theme') === theme) &&
               words.every(function (w) { return text.indexOf(w) !== -1; });
      el.hidden = !ok;
      if (ok) shown++;
    });
    stats.textContent = (shown === items.length)
      ? stats.getAttribute('data-all')
      : shown + ' of ' + items.length + ' briefs shown';
    none.hidden = shown > 0;
  }
  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      theme = chip.getAttribute('data-theme');
      chips.forEach(function (c) { c.classList.toggle('active', c === chip); });
      apply();
    });
  });
  q.addEventListener('input', apply);
})();
"""


def head(title, description):
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>%s</title>
  <meta name="description" content="%s">
  <meta name="theme-color" content="#0a0a0a">
  <link rel="icon" href="../logo.png">
  <link rel="apple-touch-icon" href="../logo.png">
  <link rel="stylesheet" href="library.css">
</head>
""" % (esc(title), esc(description, True))


def foot():
    return ('\n    <div class="foot">\n      <p class="disc">%s</p>\n      <p>%s</p>\n    </div>\n  </div>\n'
            % (esc(DISCLAIMER), esc(NOTE)))


def plural(n, word="brief"):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def clip(text, limit=200):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"


def render_index(briefs):
    n = len(briefs)
    parts = [head("Export Control Library \u2014 jcsylvan.ai",
                  "A daily export control brief, kept as a growing research library.")]
    parts.append("""<body>
  <div class="wrap">
    <a href="../tools.html" class="back-link">&larr; tools</a>
    <h1>Export Control Library</h1>
    <p class="subtitle">
      A memo to file on U.S. export controls, one each morning. Part One records the developments of
      the preceding day with labels, dates and links to the source. Part Two teaches one point of
      export control law in depth, following a fixed curriculum across the ITAR, the EAR, sanctions,
      enforcement, allied regimes, and the places where export controls meet cybersecurity and
      government contracts. This index lists every brief with the rule it teaches and the
      instruments it cites.
    </p>
""")
    if briefs:
        all_text = "%s. Latest: %s." % (plural(n), long_date(briefs[0].meta["date"]))
        parts.append("""    <div class="toolbar">
      <input id="q" class="search" type="search" autocomplete="off"
             placeholder="Search topics, rules and citations" aria-label="Search the library">
    </div>
    <div class="chips" role="group" aria-label="Filter by curriculum theme">
      <button class="chip active" type="button" data-theme="">All</button>
""")
        counts = {k: sum(1 for b in briefs if b.meta["theme"] == k) for k in THEMES}
        for letter, name in THEMES.items():
            disabled = "" if counts[letter] else " disabled"
            parts.append('      <button class="chip" type="button" data-theme="%s"%s>%s &middot; %s</button>\n'
                         % (letter, disabled, letter, esc(name)))
        parts.append("    </div>\n")
        parts.append('    <p class="stats" id="stats" data-all="%s">%s</p>\n' % (esc(all_text, True), esc(all_text)))
        parts.append('    <div class="briefs">\n')
        for b in briefs:
            m = b.meta
            search = " ".join([m["date"], long_date(m["date"]), b.heading, m["topic"], m["rule"],
                               m.get("instruments", ""), THEMES[m["theme"]]]).lower()
            parts.append('      <a class="brief" href="%s" data-theme="%s" data-text="%s">\n'
                         % (esc(b.page, True), m["theme"], esc(" ".join(search.split()), True)))
            parts.append('        <span class="when">%s &middot; <span class="theme">%s &middot; %s</span></span>\n'
                         % (esc(long_date(m["date"])), m["theme"], esc(THEMES[m["theme"]])))
            parts.append('        <span class="topic">%s <span class="arrow">&rarr;</span></span>\n' % esc(b.heading))
            parts.append('        <span class="rule">%s</span>\n' % esc(m["rule"]))
            if m.get("instruments"):
                parts.append('        <span class="instruments">Cites: %s</span>\n' % esc(m["instruments"]))
            parts.append("      </a>\n")
        parts.append("    </div>\n")
        parts.append('    <p class="empty" id="none" hidden>No brief matches that search.</p>\n')
    else:
        parts.append('    <p class="empty">The first brief will appear here after the next morning run.</p>\n')

    parts.append("""
    <section class="coverage">
      <h2>Curriculum coverage</h2>
      <p class="note">
        The point of law each day comes from a nine-theme curriculum. These are the topics taught so far.
      </p>
""")
    for letter, name in THEMES.items():
        mine = sorted((b for b in briefs if b.meta["theme"] == letter),
                      key=lambda b: (b.meta["date"], b.slug))
        cls = "theme-block" if mine else "theme-block none"
        count = plural(len(mine)) if mine else "not started"
        parts.append('      <div class="%s">\n        <div class="theme-head"><span class="letter">%s</span>'
                     '<span>%s</span><span class="n">%s</span></div>\n' % (cls, letter, esc(name), count))
        if mine:
            parts.append("        <ul>\n")
            for b in mine:
                parts.append('          <li><a href="%s">%s</a> <span class="d">%s</span></li>\n'
                             % (esc(b.page, True), esc(b.heading), esc(short_date(b.meta["date"]))))
            parts.append("        </ul>\n")
        parts.append("      </div>\n")
    parts.append("    </section>\n")
    parts.append(foot())
    if briefs:
        parts.append("  <script>\n%s  </script>\n" % INDEX_SCRIPT)
    parts.append("</body>\n</html>\n")
    return "".join(parts)


def render_brief(brief, newer, older):
    m = brief.meta
    body_html, _ = render_body(brief.body)
    parts = [head("%s \u2014 Export Control Library \u2014 jcsylvan.ai" % brief.heading, clip(m["rule"]))]
    parts.append("""<body>
  <div class="wrap">
    <a href="./" class="back-link">&larr; library</a>
    <p class="kicker">Export Control Brief &middot; %s</p>
    <h1>%s</h1>
    <div class="facts">
      <div><span class="label">Theme:</span> %s &middot; %s</div>
      <div><span class="label">Rule taught:</span> %s</div>
""" % (esc(long_date(m["date"])), esc(brief.heading), m["theme"], esc(THEMES[m["theme"]]), esc(m["rule"])))
    if m.get("instruments") or m["topic"] != brief.heading:
        parts.append("      <details>\n        <summary>Index entry</summary>\n")
        if m["topic"] != brief.heading:
            parts.append('        <p><span class="label">Curriculum topic:</span> %s</p>\n' % esc(m["topic"]))
        if m.get("instruments"):
            parts.append('        <p><span class="label">Instruments cited:</span> %s</p>\n' % esc(m["instruments"]))
        parts.append("      </details>\n")
    parts.append("    </div>\n\n    <article class=\"memo\">\n%s\n    </article>\n" % body_html)
    if newer or older:
        parts.append('\n    <nav class="pager" aria-label="Other briefs">\n')
        if older:
            parts.append('      <a class="prev" href="%s"><span class="dir">&larr; EARLIER</span>%s</a>\n'
                         % (esc(older.page, True), esc(older.heading)))
        if newer:
            parts.append('      <a class="next" href="%s"><span class="dir">LATER &rarr;</span>%s</a>\n'
                         % (esc(newer.page, True), esc(newer.heading)))
        parts.append("    </nav>\n")
    parts.append(foot())
    parts.append("</body>\n</html>\n")
    return "".join(parts)


def render_manifest(briefs):
    data = {
        "library": SECTION_URL,
        "updated": briefs[0].meta["date"] if briefs else "",
        "count": len(briefs),
        "briefs": [
            {
                "date": b.meta["date"],
                "docx": b.meta.get("docx", ""),
                "theme": b.meta["theme"],
                "title": b.meta["title"],
                "page": b.page,
                "url": SECTION_URL + b.page,
            }
            for b in briefs
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_all(briefs):
    """Every generated file, as {name: text}."""
    files = {"library.css": CSS, "index.html": render_index(briefs), "library.json": render_manifest(briefs)}
    for i, brief in enumerate(briefs):
        newer = briefs[i - 1] if i > 0 else None
        older = briefs[i + 1] if i + 1 < len(briefs) else None
        files[brief.page] = render_brief(brief, newer, older)
    return files


def build(out_dir):
    briefs = load_sources(out_dir / SRC_DIRNAME)
    files = render_all(briefs)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out_dir / name).write_text(text, encoding="utf-8")
    # A page whose source is gone is stale. Only dated brief pages are ever removed.
    for path in out_dir.glob("*.html"):
        if path.name not in files and re.match(r"\d{4}-\d{2}-\d{2}-", path.name):
            path.unlink()
    return briefs


# --------------------------------------------------------------------------
# Adding briefs
# --------------------------------------------------------------------------

def leftover_company(brief):
    hits = []
    for line in brief.body.split("\n"):
        if re.search(r"\b[Tt]he [Cc]ompany\b", line):
            hits.append(clip(line, 160))
    return hits


def add_brief(raw, out_dir, where, force=False):
    brief = parse_brief(raw, where)
    claimed = brief.meta.pop("fingerprint", "")
    replace = force or brief.meta.pop("replace", "").lower() in ("yes", "true", "1")
    brief.meta.pop("replace", None)
    validate(brief, where)
    if claimed:
        actual = fingerprint(raw)
        if claimed.lower() != actual:
            raise BriefError(
                "%s: fingerprint mismatch. The header says %s but this copy of the text gives %s. "
                "The words changed somewhere between the sender and this file. Nothing was added. "
                "Compare the file with the message word for word, fix the copy, and run add again."
                % (where, claimed, actual))
    brief.body = apply_generic_subs(brief.body)
    for key in ("title", "topic", "rule", "instruments"):
        if brief.meta.get(key):
            brief.meta[key] = apply_generic_subs(brief.meta[key])

    src_dir = out_dir / SRC_DIRNAME
    src_dir.mkdir(parents=True, exist_ok=True)
    target = src_dir / (brief.stem + ".md")
    new_text = brief.text()
    same_day = list(src_dir.glob(brief.meta["date"] + "-*.md"))
    # The same words already on file count as the same brief, whatever the quotes or spacing.
    for path in same_day:
        if fingerprint(path.read_text(encoding="utf-8")) == fingerprint(new_text) and not replace:
            return parse_brief(path.read_text(encoding="utf-8"), path.name), \
                "already published, nothing to change", bool(claimed)
    if target.exists() and target.read_text(encoding="utf-8") == new_text:
        return brief, "already published, nothing to change", bool(claimed)
    if same_day and not replace:
        raise BriefError(
            "%s: a different brief is already published for %s (%s). Nothing was changed. "
            "To replace it on purpose, add the header line 'replace: yes'."
            % (where, brief.meta["date"], ", ".join(p.name for p in same_day)))
    for path in same_day:
        if path != target:
            path.unlink()
    target.write_text(new_text, encoding="utf-8")
    return brief, ("replaced" if same_day else "added"), bool(claimed)


def cmd_add(paths, out_dir):
    results = []
    for path in paths:
        parts = split_payload(Path(path).read_text(encoding="utf-8"))
        if not parts:
            raise BriefError("%s: no brief found in the file" % path)
        for n, raw in enumerate(parts, 1):
            where = "%s (brief %d of %d)" % (Path(path).name, n, len(parts))
            results.append(add_brief(raw, out_dir, where))
    briefs = build(out_dir)
    for brief, status, verified in results:
        print("%s  %s  [%s; %s]" % (brief.meta["date"], brief.page, status,
                                    "fingerprint verified" if verified else "no fingerprint supplied"))
        for line in leftover_company(brief):
            print("    note: still says 'the company': %s" % line)
    print("The library now holds %s." % plural(len(briefs)))
    return 0


def cmd_check(out_dir):
    briefs = load_sources(out_dir / SRC_DIRNAME)
    problems, notes = [], []
    seen_dates = {}
    for b in briefs:
        seen_dates.setdefault(b.meta["date"], []).append(b.stem)
        _, page = render_body(b.body)
        missing = sorted(page.seen_refs - page.note_ids, key=lambda s: (len(s), s))
        if missing:
            problems.append("%s: endnote mark with no endnote: %s" % (b.stem, ", ".join(missing)))
        unused = sorted(page.note_ids - page.seen_refs, key=lambda s: (len(s), s))
        if unused:
            notes.append("%s: endnote never cited in the text: %s" % (b.stem, ", ".join(unused)))
        if not page.note_ids:
            notes.append("%s: no endnotes found" % b.stem)
        if not b.meta["title"].startswith("Export Control Brief for "):
            notes.append("%s: title does not start with 'Export Control Brief for'" % b.stem)
        if re.search(r"<\s*/?\s*(table|div|span|p|br|a)\b", b.body, re.I):
            notes.append("%s: the text contains raw HTML, which is shown as plain text" % b.stem)
        for line in leftover_company(b):
            problems.append("%s: still says 'the company': %s" % (b.stem, line))
    for day, stems in seen_dates.items():
        if len(stems) > 1:
            problems.append("more than one brief dated %s: %s" % (day, ", ".join(stems)))
    expected = render_all(briefs)
    for name, text in expected.items():
        path = out_dir / name
        if not path.exists():
            problems.append("%s is missing. Run: python3 tools/build_site.py build" % name)
        elif path.read_text(encoding="utf-8") != text:
            problems.append("%s does not match its source. Run: python3 tools/build_site.py build" % name)
    for path in out_dir.glob("*.html"):
        if path.name not in expected:
            problems.append("%s has no source file in %s/" % (path.name, SRC_DIRNAME))
    for line in notes:
        print("note: " + line)
    for line in problems:
        print("PROBLEM: " + line)
    print("%s checked, %d problem%s." % (plural(len(briefs)), len(problems), "" if len(problems) == 1 else "s"))
    return 1 if problems else 0


# --------------------------------------------------------------------------
# Word memos (needs pandoc)
# --------------------------------------------------------------------------

def _pandoc(path, fmt):
    try:
        done = subprocess.run(["pandoc", str(path), "-s", "-t", fmt, "--wrap=none"],
                              check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise BriefError("pandoc is needed to read Word files and is not installed")
    except subprocess.CalledProcessError as err:
        raise BriefError("pandoc could not read %s: %s" % (path, err.stderr.strip()[:200]))
    return done.stdout


class _Rows(HTMLParser):
    """Collect the text of every table row in an HTML document."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def read_index_rows(index_path):
    """The index table as {memo file name: row}, from the Word index."""
    parser = _Rows()
    parser.feed(_pandoc(index_path, "html5"))
    rows = {}
    for cells in parser.rows:
        if len(cells) >= 5 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            row = {"date": cells[0], "docx": cells[1], "instruments": cells[3], "rule": cells[4]}
            m = re.match(r"^([A-I])\.\s*(.*)$", cells[2])
            if m:
                row["theme"] = m.group(1)
                rest = m.group(2)
                name = THEMES[m.group(1)]
                if rest.lower().startswith(name.lower()):
                    rest = rest[len(name):].lstrip(" :.,")
                row["topic"] = rest[:1].upper() + rest[1:] if rest else name
            rows[cells[1]] = row
            rows.setdefault(cells[0], row)
    return rows


def docx_to_brief(path, rows=None):
    """A Word memo as brief text: header from the file name and the index, body from pandoc."""
    path = Path(path)
    m = DOCX_NAME_RE.match(path.name)
    if not m:
        raise BriefError("%s: not named 'YYYY-MM-DD Export Control Brief - topic.docx'" % path.name)
    day, short_topic = m.group(1), m.group(2)
    text = _pandoc(path, "gfm")
    title = ""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            block, text = text[4:end], text[end + 4:]
            tm = re.search(r"^title:\s*(.+)$", block, re.M)
            if tm:
                title = tm.group(1).strip()
                if len(title) >= 2 and title[0] == title[-1] and title[0] in "\"'":
                    title = title[1:-1].replace('\\"', '"').replace("''", "'")
                if title in (">", ">-", "|", "|-"):
                    title = " ".join(l.strip() for l in block[tm.end():].split("\n") if l.startswith(" "))
    body = text.strip("\n")
    if not title:
        title = "Export Control Brief for %s: %s" % (long_date(day), short_topic)
    row = (rows or {}).get(path.name) or (rows or {}).get(day) or {}
    heading = re.sub(r"^Export Control Brief for [^:]+:\s*", "", title)
    rule = row.get("rule", "")
    if not rule:
        rm = re.search(r"^#{1,3}\s*Rule\s*\n+(.+?)(?=\n#|\Z)", body, re.M | re.S)
        if rm:
            rule = " ".join(_unescape(rm.group(1)).split())
    lines = ["---", "date: %s" % day, "title: %s" % title, "theme: %s" % row.get("theme", ""),
             "topic: %s" % (row.get("topic") or heading), "instruments: %s" % row.get("instruments", ""),
             "rule: %s" % rule, "docx: %s" % path.name, "---", "", body, ""]
    return "\n".join(lines)


def cmd_from_docx(args):
    if not args:
        raise BriefError("from-docx needs a Word memo")
    memo = Path(args[0])
    index = Path(args[1]) if len(args) > 1 else memo.parent / INDEX_DOCX
    rows = read_index_rows(index) if index.exists() else {}
    sys.stdout.write(docx_to_brief(memo, rows))
    return 0


def cmd_import_folder(src, out_dir):
    index = src / INDEX_DOCX
    rows = read_index_rows(index) if index.exists() else {}
    memos = sorted(p for p in src.iterdir() if DOCX_NAME_RE.match(p.name) and p.suffix == ".docx")
    if not memos:
        raise BriefError("%s: no 'YYYY-MM-DD Export Control Brief - *.docx' files found" % src)
    failed = 0
    for memo in memos:
        try:
            brief, status, _ = add_brief(docx_to_brief(memo, rows), out_dir, memo.name, force=True)
            print("%s  %s  [%s]" % (brief.meta["date"], brief.page, status))
        except BriefError as err:
            failed += 1
            print("SKIPPED: %s" % err)
    briefs = build(out_dir)
    print("The library now holds %s.%s" % (plural(len(briefs)),
                                           " %d memo(s) were skipped." % failed if failed else ""))
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    command = argv[1]
    try:
        if command == "build":
            briefs = build(OUT)
            print("Built %s into %s/." % (plural(len(briefs)), OUT.relative_to(ROOT)))
            return 0
        if command == "add":
            if len(argv) < 3:
                raise BriefError("add needs at least one file")
            return cmd_add(argv[2:], OUT)
        if command == "check":
            return cmd_check(OUT)
        if command == "fingerprint":
            if len(argv) != 3:
                raise BriefError("fingerprint needs one file")
            print(fingerprint(Path(argv[2]).read_text(encoding="utf-8")))
            return 0
        if command == "from-docx":
            return cmd_from_docx(argv[2:])
        if len(argv) == 3 and Path(argv[1]).is_dir():
            return cmd_import_folder(Path(argv[1]), Path(argv[2]).resolve())
        raise BriefError("unknown command %r. Run with --help for the list." % command)
    except BriefError as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
