# Weekly "reads" updater

`reads.html` renders a reading list from `reads.json`. The list on the
main page is reached via the **📖 puck is here** button on `index.html`.

> **How this runs:** this job is meant to be run **once a week, on Sunday
> morning**, by a scheduled Claude Code session (a weekly trigger pointed at this
> repo). The page itself is a static file and cannot fetch new articles on its
> own — this runbook is what actually pulls them in. New articles are
> **committed straight to `main`**, so they appear on the live page automatically
> with no review step.

> **This is a growing syllabus, not a feed:** never delete or replace older
> entries. Each week's articles are **appended** so the list accumulates into a
> long reading list over time.

## TL;DR weekly run

1. `git pull` so you're on the latest `main`.
2. For each of the seven categories below, find one strong, recent article
   (≈ the last week), preferring arXiv/SSRN — see **Sourcing**.
3. Write a 2–3 sentence summary of each (see **Article schema**).
4. **Append** one object per category to the `articles` array in `reads.json`,
   using today's date in the `id` and `date` fields. Leave all existing entries
   untouched.
5. Set the top-level `"updated"` field to today's date.
6. Validate the JSON, then **commit straight to `main` and push** with a
   message like `reads: weekly update YYYY-MM-DD`.
7. **Email a recap** to jcsylvan@gmail.com — see **Weekly email** below.

## Weekly email

After the push succeeds, send a short email to **jcsylvan@gmail.com** so the
update lands in the inbox without having to check the page:

- **Subject:** `reads — weekly update YYYY-MM-DD`
- **Body:** one line per category with the article title, source, and link
  (e.g. `AI — "Title" (arXiv) https://…`), and a closing line linking to the
  page: `https://jcsylvan.ai/reads.html`.
- Use whatever email tool is available to the session (e.g. the Gmail
  integration). If no email tool is available that run, skip this step — the
  commit is the source of truth and the page is already updated.

## The job

Each run, add **one genuinely interesting, recent article for each of these
seven categories**:

- `AI`
- `Semiconductors`
- `Biotechnology`
- `Sustainable Energy`
- `Defense Tech`
- `Quantum Computing`
- `Materials Science`

### Sourcing — prefer primary research (arXiv / SSRN)

For each category, **try arXiv and SSRN first**, and only fall back to a quality
news article if nothing strong/recent turns up in that category. Prefer work
from roughly the past week so the list keeps moving.

- **arXiv** is the primary source for the technical categories. The raw API host
  (`export.arxiv.org`) is blocked by this environment's network allowlist, so do
  **not** curl it. Instead use the `WebSearch` tool with
  `allowed_domains: ["arxiv.org"]`, then take the `arxiv.org/abs/<id>` link.
  Prefer recent submissions (an id like `26MM.NNNNN` is year 26, month MM).
- **SSRN** suits the policy / economics / legal categories. Search with
  `allowed_domains: ["ssrn.com", "papers.ssrn.com"]` and take the
  `papers.ssrn.com/...abstract_id=<n>` (or `ssrn.com/abstract=<n>`) link.
- **News fallback:** if neither has anything good that day for a category, use a
  reputable news article instead — that's acceptable, not a failure.

Suggested source per category (a guide, not a hard rule):

| Category            | Primary source | arXiv areas |
|---------------------|----------------|-------------|
| AI                  | arXiv          | `cs.AI`, `cs.LG`, `cs.CL` |
| Semiconductors      | arXiv          | `cond-mat.mes-hall`, `physics.app-ph`, `eess` |
| Biotechnology       | arXiv          | `q-bio`, `cs.LG` (bio-ML) |
| Sustainable Energy  | arXiv / SSRN   | `eess.SY`, `physics`, `cond-mat`; SSRN for energy economics/policy |
| Defense Tech        | SSRN / arXiv   | SSRN for policy/law/economics; arXiv `cs`/`eess` for technical |
| Quantum Computing   | arXiv          | `quant-ph` |
| Materials Science   | arXiv          | `cond-mat.mtrl-sci`, `cond-mat.supr-con` |

### How to do it

1. For each category, search arXiv/SSRN as above (prefer recent, reputable, a
   real working URL); use a news article only as a fallback.
2. **Write a short AI summary** of each article (see below) — read the abstract
   or article and condense it.
3. Append one object per category to the `articles` array in `reads.json`.
4. Set `"source"` to the origin — e.g. `"arXiv · 2601.12538"`, `"SSRN"`, or the
   publication name when using a news fallback.
5. Set the top-level `"updated"` field to today's date.
6. Validate the file parses as JSON (e.g. `python3 -m json.tool reads.json`),
   then **commit straight to `main` and push** — no PR needed.

### Article schema

```json
{
  "id": "YYYY-MM-DD-<category-slug>",
  "date": "YYYY-MM-DD",
  "category": "AI",
  "title": "Exact article headline",
  "source": "Publication name",
  "url": "https://…",
  "summary": "2–3 sentence plain-language summary of the article."
}
```

Notes:
- `summary` is **required**: 2–3 sentences (~40–70 words), plain language, no
  hype. Capture what the work does and why it matters. It's pre-generated here
  and stored in `reads.json` — the page just renders it behind an expandable
  "AI summary" toggle. (The site is a static page with no backend, so summaries
  are written at update time, not in the browser, and never expose an API key.)
  If a summary is omitted, the row simply shows no toggle.
- `id` must be unique and stable — `date` + lowercase, hyphenated category
  (e.g. `2026-05-30-sustainable-energy`). The site keys read-status and
  comments off `id`, so never reuse or change an existing `id`. Because the date
  is part of the `id`, a once-a-week run never collides with previous weeks.
  (If you ever run twice in one day, append `-2` to disambiguate.)
- Category slugs used by the styling: `ai`, `semiconductors`, `biotechnology`,
  `sustainable-energy`, `defense-tech`, `quantum-computing`, `materials-science`.
- Don't remove old entries — the list is meant to accumulate.
- Avoid duplicating a URL that's already in the file.

The reader's read/unread checkboxes and comments are stored in the browser's
local storage, so they are never written back to `reads.json`.
