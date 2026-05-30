# Daily "reads" updater

`reads.html` renders a daily reading list from `reads.json`. The list on the
main page is reached via the **📖 reads** button on `index.html`.

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

### How to do it

1. Web-search for a recent, high-quality article in each category (prefer the
   last day or two; reputable sources; a real, working URL).
2. Append one object per category to the `articles` array in `reads.json`.
3. Set the top-level `"updated"` field to today's date.
4. Commit and push.

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
