#!/usr/bin/env python3
"""Validate reads.json before committing a weekly update.

Run from the repo root:

    python3 validate_reads.py

Exit code 0 means the file is safe to commit. Exit code 1 means at least one
error was found — fix the offending entry before committing. Warnings are
printed but never fail the run.

No third-party dependencies.
"""

import json
import os
import re
import sys
from collections import defaultdict

FILENAME = "reads.json"

REQUIRED_FIELDS = ("id", "date", "category", "title", "source", "url", "summary")

VALID_CATEGORIES = (
    "AI",
    "Semiconductors",
    "Biotechnology",
    "Sustainable Energy",
    "Defense Tech",
    "Quantum Computing",
    "Materials Science",
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# id = <date>-<category-slug> with an optional -2 / -3 ... for same-day reruns.
ID_SUFFIX_RE = re.compile(r"^(?P<slug>.+?)(?:-(?P<rerun>[2-9]|[1-9]\d+))?$")

SUMMARY_MIN_WORDS = 35
SUMMARY_MAX_WORDS = 80

ARXIV_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?",
    re.IGNORECASE,
)
SSRN_RE = re.compile(
    r"(?:abstract_id=|ssrn\.com/abstract=)(?P<id>\d+)",
    re.IGNORECASE,
)


def category_slug(category):
    """'Sustainable Energy' -> 'sustainable-energy'."""
    return re.sub(r"[^a-z0-9]+", "-", category.lower()).strip("-")


def normalize_url(url):
    """Collapse the different link forms of the same paper to one identifier.

    arxiv.org/abs/2608.24747, arxiv.org/pdf/2608.24747 and .../abs/2608.24747v2
    all normalize to 'arxiv:2608.24747'. SSRN abstract_id=6227318 and
    ssrn.com/abstract=6227318 both normalize to 'ssrn:6227318'. Anything else
    falls back to a lightly-cleaned version of the URL itself.
    """
    match = ARXIV_RE.search(url)
    if match:
        return "arxiv:" + match.group("id")
    match = SSRN_RE.search(url)
    if match:
        return "ssrn:" + match.group("id")
    cleaned = url.strip().lower().rstrip("/")
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = re.sub(r"^www\.", "", cleaned)
    return "url:" + cleaned


def label(index, article):
    """Human-readable handle for an article, even when its id is missing."""
    if isinstance(article, dict):
        article_id = article.get("id")
        if isinstance(article_id, str) and article_id.strip():
            return article_id.strip()
    return "articles[%d] (no id)" % index


def is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def report_duplicates(errors, groups, what):
    for key, entries in sorted(groups.items()):
        if len(entries) > 1:
            errors.append(
                "duplicate %s %s shared by: %s" % (what, key, ", ".join(entries))
            )


def validate(path):
    errors = []
    warnings = []

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return ["%s not found (run this from the repo root)" % path], [], None
    except json.JSONDecodeError as exc:
        return ["%s does not parse as JSON: %s" % (path, exc)], [], None

    if not isinstance(data, dict):
        return ["%s must contain a JSON object at the top level" % path], [], None

    if "schema" not in data:
        warnings.append('top-level "schema" key is absent')

    if "updated" not in data:
        errors.append('missing top-level "updated"')
    elif is_empty(data["updated"]):
        errors.append('top-level "updated" is empty')
    elif not (isinstance(data["updated"], str) and DATE_RE.match(data["updated"])):
        errors.append('top-level "updated" is not YYYY-MM-DD: %r' % (data["updated"],))

    if "articles" not in data:
        errors.append('missing top-level "articles"')
        return errors, warnings, None

    articles = data["articles"]
    if not isinstance(articles, list):
        errors.append('top-level "articles" must be an array')
        return errors, warnings, None

    by_id = defaultdict(list)
    by_url = defaultdict(list)
    by_source_id = defaultdict(list)
    dates = set()

    for index, article in enumerate(articles):
        name = label(index, article)

        if not isinstance(article, dict):
            errors.append("articles[%d] is not an object" % index)
            continue

        missing = [f for f in REQUIRED_FIELDS if f not in article]
        empty = [f for f in REQUIRED_FIELDS if f in article and is_empty(article[f])]
        for field in missing:
            errors.append("%s: missing required field %r" % (name, field))
        for field in empty:
            errors.append("%s: empty value for required field %r" % (name, field))

        date = article.get("date")
        if isinstance(date, str) and date.strip():
            if DATE_RE.match(date):
                dates.add(date)
            else:
                errors.append("%s: date %r is not YYYY-MM-DD" % (name, date))

        category = article.get("category")
        if isinstance(category, str) and category.strip():
            if category not in VALID_CATEGORIES:
                errors.append(
                    "%s: category %r is not one of: %s"
                    % (name, category, ", ".join(VALID_CATEGORIES))
                )

        article_id = article.get("id")
        if isinstance(article_id, str) and article_id.strip():
            article_id = article_id.strip()
            by_id[article_id].append(name)

            if isinstance(date, str) and DATE_RE.match(date or ""):
                if not article_id.startswith(date + "-"):
                    errors.append(
                        "%s: id prefix does not match its date %s" % (name, date)
                    )
                else:
                    tail = article_id[len(date) + 1 :]
                    match = ID_SUFFIX_RE.match(tail)
                    tail_slug = match.group("slug") if match else tail
                    if isinstance(category, str) and category in VALID_CATEGORIES:
                        expected = category_slug(category)
                        if tail_slug != expected:
                            errors.append(
                                "%s: id suffix %r does not match category %r "
                                "(expected %r)" % (name, tail, category, expected)
                            )

        url = article.get("url")
        if isinstance(url, str) and url.strip():
            url = url.strip()
            by_url[url].append(name)
            by_source_id[normalize_url(url)].append((name, url))

        summary = article.get("summary")
        if isinstance(summary, str) and summary.strip():
            words = len(summary.split())
            if words < SUMMARY_MIN_WORDS or words > SUMMARY_MAX_WORDS:
                warnings.append(
                    "%s: summary is %d words (expected roughly %d-%d)"
                    % (name, words, SUMMARY_MIN_WORDS, SUMMARY_MAX_WORDS)
                )

    report_duplicates(errors, by_id, "id")
    report_duplicates(errors, by_url, "url")

    # Only report a normalized clash the raw-url check above did not already show
    # (i.e. the same paper reached through two *different* link forms).
    for key, entries in sorted(by_source_id.items()):
        names = [name for name, _ in entries]
        urls = {url for _, url in entries}
        if len(entries) > 1 and len(urls) > 1:
            errors.append(
                "duplicate paper %s (same source under different link forms) "
                "shared by: %s" % (key, ", ".join(names))
            )

    stats = {"articles": len(articles), "dates": len(dates)}
    return errors, warnings, stats


def main():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), FILENAME)
    if len(sys.argv) > 1:
        path = sys.argv[1]

    errors, warnings, stats = validate(path)

    if warnings:
        print("WARNINGS (%d):" % len(warnings))
        for warning in warnings:
            print("  - %s" % warning)
        print("")

    if errors:
        print("ERRORS (%d) - do not commit:" % len(errors))
        for error in errors:
            print("  - %s" % error)
        print("")
        print("FAILED: %s has %d error(s)." % (os.path.basename(path), len(errors)))
        return 1

    print(
        "%s: %d articles, %d distinct dates, %d warning(s) - OK"
        % (os.path.basename(path), stats["articles"], stats["dates"], len(warnings))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
