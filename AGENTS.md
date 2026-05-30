# Daily "reads" updater

`reads.html` renders a daily reading list from `reads.json`. The list on the
main page is reached via the **📖 puck is here** button on `index.html`.

## Daily job

Once per day, add **one genuinely interesting, recent article for each of these
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
news article if nothing strong/recent turns up in that category.

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
2. Append one object per category to the `articles` array in `reads.json`.
3. Set `"source"` to the origin — e.g. `"arXiv · 2601.12538"`, `"SSRN"`, or the
   publication name when using a news fallback.
4. Set the top-level `"updated"` field to today's date.
5. Commit and push.

### Article schema

```json
{
  "id": "YYYY-MM-DD-<category-slug>",
  "date": "YYYY-MM-DD",
  "category": "AI",
  "title": "Exact article headline",
  "source": "Publication name",
  "url": "https://…"
}
```

Notes:
- `id` must be unique and stable — `date` + lowercase, hyphenated category
  (e.g. `2026-05-30-sustainable-energy`). The site keys read-status and
  comments off `id`, so never reuse or change an existing `id`.
- Category slugs used by the styling: `ai`, `semiconductors`, `biotechnology`,
  `sustainable-energy`, `defense-tech`, `quantum-computing`, `materials-science`.
- Don't remove old entries — the list is meant to accumulate.
- Avoid duplicating a URL that's already in the file.

The reader's read/unread checkboxes and comments are stored in the browser's
local storage, so they are never written back to `reads.json`.
