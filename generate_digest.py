#!/usr/bin/env python3
"""
Weekly Digest Generator for AI Quality Jobs board.

Pulls fresh jobs from all 3 APIs, filters to ones posted in the last 7 days,
ranks by AI-quality category relevance, generates:
  - Markdown email digest (for Buttondown/Mailchimp copy/paste)
  - HTML digest (for richer email clients)
  - Plain text digest (for SMS / fallback)

Cron-suggested: every Monday 09:00 (Asia)
Output: products/ai-quality-jobs-landing/digest/digest-{YYYY-MM-DD}.{md,html,txt}

No external dependencies beyond stdlib + urllib.
"""

import json
import re
import sys
import os
import urllib.request
import urllib.error
import ssl
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

# ---------- API FETCH ----------

UA = {"User-Agent": "Mozilla/5.0 (Discus/1.0; +https://aldow3n-a11y.github.io/ai-quality-jobs/)"}

SOURCES = [
    ("ROK", "https://www.remoteok.com/api?tag=ai"),
    ("REM", "https://remotive.com/api/remote-jobs?category=software-dev"),
    ("JOB", "https://jobicy.com/api/v2/remote-jobs?count=50"),
]


def fetch(url, timeout=15):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", errors="replace")


def pull_remoteok():
    """RemoteOK returns raw list — normalize."""
    try:
        raw = json.loads(fetch(SOURCES[0][1]))
    except Exception as e:
        print(f"[warn] RemoteOK: {e}", file=sys.stderr)
        return []
    out = []
    for j in raw[1:] if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "legal" in raw[0] else raw:
        if not isinstance(j, dict):
            continue
        epoch = j.get("epoch") or j.get("date")
        try:
            posted = datetime.fromtimestamp(int(epoch), tz=timezone.utc).date()
        except Exception:
            continue
        desc = re.sub(r"<[^>]+>", " ", str(j.get("description", "")))
        out.append({
            "src": "ROK",
            "id": str(j.get("id") or j.get("slug") or ""),
            "title": str(j.get("position") or "").strip(),
            "company": str(j.get("company") or "").strip(),
            "tags": [str(t) for t in (j.get("tags") or []) if t][:8],
            "url": str(j.get("url") or "").strip(),
            "posted": posted,
            "desc": desc.strip()[:600],
        })
    return out


def pull_remotive():
    """Remotive returns {jobs: [...]}."""
    try:
        raw = json.loads(fetch(SOURCES[1][1]))
    except Exception as e:
        print(f"[warn] Remotive: {e}", file=sys.stderr)
        return []
    out = []
    for j in raw.get("jobs", []):
        try:
            posted = datetime.fromisoformat(str(j.get("publication_date") or "").replace("Z", "+00:00")).date()
        except Exception:
            continue
        desc = re.sub(r"<[^>]+>", " ", str(j.get("description") or ""))
        out.append({
            "src": "REM",
            "id": str(j.get("id") or ""),
            "title": str(j.get("title") or "").strip(),
            "company": str(j.get("company_name") or "").strip(),
            "tags": [str(t) for t in (j.get("tags") or []) if t][:8],
            "url": str(j.get("url") or "").strip(),
            "posted": posted,
            "desc": desc.strip()[:600],
        })
    return out


def pull_jobicy():
    """Jobicy returns {jobs: [...]}."""
    try:
        raw = json.loads(fetch(SOURCES[2][1]))
    except Exception as e:
        print(f"[warn] Jobicy: {e}", file=sys.stderr)
        return []
    out = []
    for j in raw.get("jobs", []):
        try:
            posted = datetime.fromisoformat(str(j.get("pubDate") or "").replace("Z", "+00:00")).date()
        except Exception:
            continue
        desc = re.sub(r"<[^>]+>", " ", str(j.get("jobExcerpt") or j.get("description") or ""))
        out.append({
            "src": "JOB",
            "id": str(j.get("id") or ""),
            "title": str(j.get("jobTitle") or "").strip(),
            "company": str(j.get("companyName") or "").strip(),
            "tags": [str(t) for t in (j.get("jobIndustry") or []) if t][:8] or [str(j.get("jobType") or "")],
            "url": str(j.get("url") or "").strip(),
            "posted": posted,
            "desc": desc.strip()[:600],
        })
    return out


# ---------- CATEGORIZE ----------

# Weighted keyword scoring for AI Quality board
KW = {
    "pipeline": ["data pipeline", "etl", "dataflow", "ingestion", "labeling", "annotation", "synthetic data", "dataset"],
    "quality":  ["quality", "evaluation", "eval", "benchmark", "guardrail", "alignment", "rlhf", "rlhf", "red team", "ragas"],
    "auditor":  ["auditor", "audit", "compliance", "governance", "responsible ai", "trustworthy", "safety"],
    "editor":   ["prompt engineer", "prompt design", "annotation", "writer", "editor", "content ops", "fine-tun"],
}


def categorize(job):
    """Return primary category by keyword score."""
    text = (job["title"] + " " + " ".join(job["tags"]) + " " + job["desc"]).lower()
    scores = {cat: sum(1 for kw in words if kw in text) for cat, words in KW.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return None
    return best


# ---------- DIGEST BUILD ----------

def dedupe(jobs):
    """Dedupe by (title, company) pair."""
    seen, out = set(), []
    for j in jobs:
        key = (j["title"].lower(), j["company"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def build_digest(jobs, since_days=7):
    """Build digest content for jobs posted in the last `since_days` days."""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=since_days)
    fresh = [j for j in jobs if j["posted"] >= cutoff]
    fresh.sort(key=lambda j: j["posted"], reverse=True)

    # Group by category
    by_cat = {"quality": [], "auditor": [], "pipeline": [], "editor": []}
    for j in fresh:
        cat = categorize(j)
        if cat:
            by_cat[cat].append(j)

    # Top 5 per category for digest
    top = {cat: jobs[:5] for cat, jobs in by_cat.items()}
    return {
        "fresh": fresh,
        "by_cat": by_cat,
        "top": top,
        "total_fresh": len(fresh),
        "cutoff": cutoff,
    }


# ---------- RENDER ----------

def render_md(d, today):
    lines = []
    lines.append(f"# AI Quality Jobs — Weekly Digest ({today.isoformat()})")
    lines.append("")
    lines.append(f"**{d['total_fresh']} new roles** posted in the last 7 days across quality, audit, pipeline, and editorial categories.")
    lines.append("")
    lines.append("---")
    lines.append("")
    cat_names = {"quality": "AI Quality & Evaluation", "auditor": "AI Auditor & Governance",
                 "pipeline": "Data Pipeline & Annotation", "editor": "Prompt & Editorial"}
    for cat, jobs in d["top"].items():
        if not jobs:
            continue
        lines.append(f"## {cat_names[cat]} ({len(jobs)})")
        lines.append("")
        for j in jobs:
            tag_str = ", ".join(t for t in j["tags"] if t)[:120]
            lines.append(f"### [{escape(j['title'])}]({j['url']})")
            lines.append(f"**{escape(j['company'])}** · {j['posted'].isoformat()} · {j['src']}")
            if tag_str:
                lines.append(f"Tags: `{tag_str}`")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Browse all open roles →** https://aldow3n-a11y.github.io/ai-quality-jobs/")
    lines.append("")
    lines.append("You're getting this because you subscribed at the AI Quality Jobs board.")
    lines.append("Reply with 'pause' to skip a week, 'unsub' to stop.")
    lines.append("")
    return "\n".join(lines)


def render_html(d, today):
    """Render minimal HTML email digest."""
    parts = []
    parts.append(f"<!doctype html><html><body style=\"font-family:Inter,Arial,sans-serif;color:#1A1814;background:#F8F4EE;margin:0;padding:24px\">")
    parts.append(f"<div style=\"max-width:680px;margin:0 auto;background:#fff;padding:32px;border:1px solid #D9D2BF\">")
    parts.append(f"<h1 style=\"font-family:Georgia,serif;font-weight:500;margin:0 0 8px 0\">AI Quality Jobs — Weekly Digest</h1>")
    parts.append(f"<p style=\"font-family:monospace;font-size:12px;color:#6B6557;margin:0 0 24px 0\">{today.isoformat()} · {d['total_fresh']} new roles</p>")
    parts.append("<hr style=\"border:none;border-top:1px solid #D9D2BF;margin:24px 0\">")
    cat_names = {"quality": "AI Quality & Evaluation", "auditor": "AI Auditor & Governance",
                 "pipeline": "Data Pipeline & Annotation", "editor": "Prompt & Editorial"}
    for cat, jobs in d["top"].items():
        if not jobs:
            continue
        parts.append(f"<h2 style=\"font-family:Georgia,serif;font-weight:500;color:#1F3D2E;border-bottom:1px solid #D9D2BF;padding-bottom:8px\">{cat_names[cat]} ({len(jobs)})</h2>")
        for j in jobs:
            parts.append(f"<div style=\"margin:16px 0;padding:12px;border-left:3px solid #C2410C;background:#F8F4EE\">")
            parts.append(f"<a href=\"{escape(j['url'])}\" style=\"color:#1F3D2E;font-weight:600;font-size:16px;text-decoration:none\">{escape(j['title'])}</a>")
            parts.append(f"<div style=\"font-family:monospace;font-size:11px;color:#6B6557;margin-top:4px\">{escape(j['company'])} · {j['posted'].isoformat()} · {j['src']}</div>")
            parts.append("</div>")
    parts.append("<hr style=\"border:none;border-top:1px solid #D9D2BF;margin:24px 0\">")
    parts.append("<p><a href=\"https://aldow3n-a11y.github.io/ai-quality-jobs/\" style=\"color:#C2410C\">Browse all open roles →</a></p>")
    parts.append("<p style=\"font-size:12px;color:#6B6557\">Reply with 'pause' to skip a week, 'unsub' to stop.</p>")
    parts.append("</div></body></html>")
    return "".join(parts)


def render_txt(d, today):
    parts = []
    parts.append(f"AI QUALITY JOBS — WEEKLY DIGEST ({today.isoformat()})")
    parts.append(f"{d['total_fresh']} new roles in last 7 days")
    parts.append("=" * 60)
    cat_names = {"quality": "AI QUALITY & EVALUATION", "auditor": "AI AUDITOR & GOVERNANCE",
                 "pipeline": "DATA PIPELINE & ANNOTATION", "editor": "PROMPT & EDITORIAL"}
    for cat, jobs in d["top"].items():
        if not jobs:
            continue
        parts.append("")
        parts.append(cat_names[cat] + f" ({len(jobs)})")
        parts.append("-" * 40)
        for j in jobs:
            parts.append(f"• {j['title']}")
            parts.append(f"  {j['company']} · {j['posted'].isoformat()} · {j['src']}")
            parts.append(f"  {j['url']}")
            parts.append("")
    parts.append("=" * 60)
    parts.append("Browse all: https://aldow3n-a11y.github.io/ai-quality-jobs/")
    parts.append("Reply 'pause' or 'unsub' to manage.")
    return "\n".join(parts)


# ---------- MAIN ----------

def main():
    out_dir = Path(__file__).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching jobs from 3 sources...", file=sys.stderr)
    jobs = pull_remoteok() + pull_remotive() + pull_jobicy()
    print(f"  Pulled {len(jobs)} raw jobs", file=sys.stderr)
    jobs = dedupe(jobs)
    print(f"  {len(jobs)} after dedupe", file=sys.stderr)

    d = build_digest(jobs, since_days=7)
    today = datetime.now(timezone.utc).date()
    print(f"  {d['total_fresh']} fresh (last 7 days)", file=sys.stderr)
    for cat, lst in d["by_cat"].items():
        print(f"    {cat}: {len(lst)}", file=sys.stderr)

    md_path = out_dir / f"digest-{today.isoformat()}.md"
    html_path = out_dir / f"digest-{today.isoformat()}.html"
    txt_path = out_dir / f"digest-{today.isoformat()}.txt"

    md_path.write_text(render_md(d, today), encoding="utf-8")
    html_path.write_text(render_html(d, today), encoding="utf-8")
    txt_path.write_text(render_txt(d, today), encoding="utf-8")

    print(f"Wrote: {md_path.name}, {html_path.name}, {txt_path.name}", file=sys.stderr)
    print(f"\n=== LATEST DIGEST PREVIEW (markdown, first 30 lines) ===\n")
    print("\n".join(render_md(d, today).splitlines()[:30]))


if __name__ == "__main__":
    main()
