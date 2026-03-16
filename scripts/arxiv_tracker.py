#!/usr/bin/env python3
"""
arXiv Popularity Tracker

Fetches new arXiv papers from specified categories, ranks them by
X/Twitter engagement, emails the top result, and updates the website archive.

Intended to run daily at 3:30 AM ET via cron.
"""

import configparser
import datetime
import json
import logging
import os
import smtplib
import sys
import time
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests
import tweepy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.ini"

ARXIV_API_BASE = "http://export.arxiv.org/api/query"
MAX_RESULTS_PER_CATEGORY = 200
ABSTRACT_WORD_LIMIT = 200


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config():
    if not CONFIG_PATH.exists():
        log.error("config.ini not found. Copy config.example.ini to config.ini and fill in credentials.")
        sys.exit(1)
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg


# ---------------------------------------------------------------------------
# arXiv fetching
# ---------------------------------------------------------------------------

def fetch_arxiv_papers(categories: list[str]) -> list[dict]:
    """Fetch papers submitted in the last 24 hours for the given categories."""
    now = datetime.datetime.now(datetime.timezone.utc)
    yesterday = now - datetime.timedelta(days=1)

    # arXiv API date range format: YYYYMMDDHHMM
    date_from = yesterday.strftime("%Y%m%d0000")
    date_to = now.strftime("%Y%m%d2359")

    all_papers = {}

    for cat in categories:
        cat = cat.strip()
        query = f"cat:{cat}"
        params = {
            "search_query": query,
            "start": 0,
            "max_results": MAX_RESULTS_PER_CATEGORY,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        log.info("Fetching arXiv papers for category: %s", cat)
        try:
            resp = requests.get(ARXIV_API_BASE, params=params, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Failed to fetch category %s: %s", cat, exc)
            continue

        feed = feedparser.parse(resp.text)

        for entry in feed.entries:
            published = datetime.datetime.fromisoformat(
                entry.published.replace("Z", "+00:00")
            )
            if published < yesterday:
                continue

            arxiv_id = entry.id.split("/abs/")[-1]
            # Strip version suffix for cleaner ID
            arxiv_id_base = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id

            if arxiv_id_base in all_papers:
                continue

            authors = [a.get("name", "") for a in entry.get("authors", [])]
            all_papers[arxiv_id_base] = {
                "arxiv_id": arxiv_id_base,
                "title": entry.title.replace("\n", " ").strip(),
                "authors": authors,
                "abstract": entry.summary.replace("\n", " ").strip(),
                "published": published.isoformat(),
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id_base}.pdf",
                "abs_url": f"https://arxiv.org/abs/{arxiv_id_base}",
                "categories": [t["term"] for t in entry.get("tags", [])],
            }

        # Be polite to the arXiv API
        time.sleep(3)

    papers = list(all_papers.values())
    log.info("Fetched %d unique papers across %d categories", len(papers), len(categories))
    return papers


# ---------------------------------------------------------------------------
# X / Twitter popularity scoring
# ---------------------------------------------------------------------------

def score_papers_via_twitter(papers: list[dict], bearer_token: str) -> list[dict]:
    """Search X for mentions of each paper and compute engagement scores.

    Engagement score = mentions + 2*retweets + likes
    """
    if not bearer_token or bearer_token == "your_bearer_token_here":
        log.warning("No valid Twitter bearer token; skipping Twitter scoring.")
        for p in papers:
            p["engagement_score"] = 0
            p["mentions"] = 0
            p["retweets"] = 0
            p["likes"] = 0
        return papers

    client = tweepy.Client(bearer_token=bearer_token, wait_on_rate_limit=True)

    for paper in papers:
        query = f'"{paper["arxiv_id"]}" OR "{paper["title"][:60]}" -is:retweet'
        # Twitter search query max is 512 chars
        if len(query) > 512:
            query = f'"{paper["arxiv_id"]}" -is:retweet'

        mentions = 0
        total_retweets = 0
        total_likes = 0

        try:
            response = client.search_recent_tweets(
                query=query,
                max_results=100,
                tweet_fields=["public_metrics"],
            )

            if response.data:
                for tweet in response.data:
                    mentions += 1
                    metrics = tweet.public_metrics or {}
                    total_retweets += metrics.get("retweet_count", 0)
                    total_likes += metrics.get("like_count", 0)

        except tweepy.TooManyRequests:
            log.warning("Rate limited on Twitter API for paper %s", paper["arxiv_id"])
        except tweepy.TwitterServerError as exc:
            log.warning("Twitter server error for %s: %s", paper["arxiv_id"], exc)
        except Exception as exc:
            log.warning("Twitter search failed for %s: %s", paper["arxiv_id"], exc)

        paper["mentions"] = mentions
        paper["retweets"] = total_retweets
        paper["likes"] = total_likes
        paper["engagement_score"] = mentions + 2 * total_retweets + total_likes

        # Respect rate limits
        time.sleep(1)

    return papers


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def truncate_abstract(abstract: str, limit: int = ABSTRACT_WORD_LIMIT) -> str:
    words = abstract.split()
    if len(words) <= limit:
        return abstract
    return " ".join(words[:limit]) + " ..."


def send_email(cfg, subject: str, body_html: str):
    smtp_server = cfg.get("email", "smtp_server")
    smtp_port = cfg.getint("email", "smtp_port")
    sender = cfg.get("email", "sender_email")
    password = cfg.get("email", "sender_password")
    recipient = cfg.get("email", "recipient_email")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        log.info("Email sent to %s", recipient)
    except Exception as exc:
        log.error("Failed to send email: %s", exc)


def build_paper_email_html(paper: dict) -> str:
    abstract = truncate_abstract(paper["abstract"])
    authors = ", ".join(paper["authors"][:10])
    if len(paper["authors"]) > 10:
        authors += f" et al. ({len(paper['authors'])} total)"

    return f"""\
<html>
<body style="font-family: 'SF Mono', 'Fira Code', monospace; background: #0a0a0a; color: #e0e0e0; padding: 2rem;">
  <div style="max-width: 640px; margin: 0 auto;">
    <h2 style="color: #fff; border-bottom: 1px solid #333; padding-bottom: 0.5rem;">
      Daily arXiv Popular Paper
    </h2>
    <h3 style="color: #ccc; margin-top: 1.5rem;">{paper['title']}</h3>
    <p style="color: #888; font-size: 0.9rem;">{authors}</p>
    <p style="color: #aaa; line-height: 1.6; margin-top: 1rem;">{abstract}</p>
    <p style="margin-top: 1.5rem;">
      <a href="{paper['pdf_url']}" style="color: #6af; text-decoration: none;">
        PDF &rarr; {paper['pdf_url']}
      </a>
    </p>
    <p style="margin-top: 0.5rem;">
      <a href="{paper['abs_url']}" style="color: #6af; text-decoration: none;">
        Abstract &rarr; {paper['abs_url']}
      </a>
    </p>
    <div style="margin-top: 1.5rem; padding: 1rem; border: 1px solid #333; border-radius: 4px; background: #111;">
      <p style="color: #888; font-size: 0.85rem;">
        <strong style="color: #ccc;">Why this paper?</strong><br>
        This paper has <strong>{paper['mentions']}</strong> mention(s) on X,
        <strong>{paper['retweets']}</strong> retweet(s), and
        <strong>{paper['likes']}</strong> like(s)
        (engagement score: <strong>{paper['engagement_score']}</strong>).
      </p>
    </div>
    <p style="color: #444; font-size: 0.75rem; margin-top: 2rem;">
      Categories: {', '.join(paper.get('categories', []))}
    </p>
  </div>
</body>
</html>"""


def build_fallback_email_html(reason: str) -> str:
    return f"""\
<html>
<body style="font-family: 'SF Mono', 'Fira Code', monospace; background: #0a0a0a; color: #e0e0e0; padding: 2rem;">
  <div style="max-width: 640px; margin: 0 auto;">
    <h2 style="color: #fff; border-bottom: 1px solid #333; padding-bottom: 0.5rem;">
      Daily arXiv Popular Paper
    </h2>
    <p style="color: #888; margin-top: 1.5rem;">{reason}</p>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Website archive update
# ---------------------------------------------------------------------------

def update_archive(cfg, paper: dict | None, reason: str = ""):
    """Append today's result to the JSON data file for the archive page."""
    data_path = Path(cfg.get("website", "data_path"))

    history = []
    if data_path.exists():
        try:
            history = json.loads(data_path.read_text())
        except (json.JSONDecodeError, OSError):
            history = []

    entry = {
        "date": datetime.date.today().isoformat(),
        "paper": None,
        "fallback_reason": reason,
    }

    if paper:
        entry["paper"] = {
            "arxiv_id": paper["arxiv_id"],
            "title": paper["title"],
            "authors": paper["authors"][:10],
            "abstract": truncate_abstract(paper["abstract"]),
            "pdf_url": paper["pdf_url"],
            "abs_url": paper["abs_url"],
            "categories": paper.get("categories", []),
            "engagement_score": paper.get("engagement_score", 0),
            "mentions": paper.get("mentions", 0),
            "retweets": paper.get("retweets", 0),
            "likes": paper.get("likes", 0),
        }

    history.insert(0, entry)
    # Keep last 365 days
    history = history[:365]

    data_path.write_text(json.dumps(history, indent=2))
    log.info("Archive data updated at %s", data_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("arXiv Popularity Tracker starting")
    cfg = load_config()

    categories = [c.strip() for c in cfg.get("arxiv", "categories").split(",")]

    # Step 1: Fetch papers
    papers = fetch_arxiv_papers(categories)

    if not papers:
        reason = "No standout popular papers today. No new papers were found in the tracked categories."
        log.info(reason)
        send_email(cfg, "Daily arXiv Popular Paper", build_fallback_email_html(reason))
        update_archive(cfg, None, reason)
        return

    # Step 2: Score via Twitter/X
    bearer_token = cfg.get("twitter", "bearer_token", fallback="")
    papers = score_papers_via_twitter(papers, bearer_token)

    # Step 3: Select top paper
    papers.sort(key=lambda p: p["engagement_score"], reverse=True)
    top_paper = papers[0]

    if top_paper["engagement_score"] == 0 and len(papers) > 0:
        # No Twitter data — pick the first paper by submission date as fallback
        log.info("No Twitter engagement found; selecting most recent paper instead.")

    log.info(
        "Top paper: %s (score=%d)",
        top_paper["arxiv_id"],
        top_paper["engagement_score"],
    )

    # Step 4: Send email
    html = build_paper_email_html(top_paper)
    send_email(cfg, "Daily arXiv Popular Paper", html)

    # Step 5: Update website archive
    update_archive(cfg, top_paper)

    log.info("Done.")


if __name__ == "__main__":
    main()
