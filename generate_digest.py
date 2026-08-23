#!/usr/bin/env python3
"""
Weekly Digest Generator for AI Quality Jobs board (v2.3.1).

Improvements over v2.2:
- Added 7th category: 'gtm' (Product, Marketing & GTM roles for AI tools)
  * Catches AI Product Manager, Technical PM, AI Growth Marketer,
    AI Account Executive / Solutions Consultant / Pre-Sales,
    RevOps at AI companies, and Scientific Engagement roles at AI-native companies.
- v2.3.1: categorize() now also scans description when the AI gate passes
  via description (2+ AI hits), so titles like "Strategic Account Executive"
  or "Business Development Manager" at AI companies get categorized.
  This raised categorization rate from ~36% (4/11) to ~91% (10/11).
- Tightened engineering patterns: "generative ai" / "ai/ml" alone no longer
  trigger engineering (too broad — would match "Director, Revenue Operations"
  at Qventus because the company description mentions generative AI).
- 7 categories now: quality / auditor / pipeline / prompt / engineering / editor / gtm
- Top 5 per category (top 3 for gtm since volume is smaller)

Pulls fresh jobs from 3 APIs (RemoteOK, Remotive, Jobicy), filters to last 7 days,
generates Markdown, HTML, and Plain Text digests.

Cron-suggested: every Monday 09:00 (Asia)
Output: products/ai-quality-jobs-landing/digest/digest-{YYYY-MM-DD}.{md,html,txt}

No external dependencies beyond stdlib + urllib.
"""

import json
import re
import sys
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
    try:
        raw = json.loads(fetch(SOURCES[0][1]))
    except Exception as e:
        print(f"[warn] RemoteOK: {e}", file=sys.stderr)
        return []
    out = []
    items = raw[1:] if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "legal" in raw[0] else raw
    for j in items:
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


# ---------- CLASSIFIER v2 ----------

# Compiled patterns. Each is a list of compiled regexes.
# Match if title or tags hit at least one pattern in this category.
# Patterns use word boundaries (\b) to prevent substring false positives
# (e.g. "medical" won't match a "quality" pattern).

CATS = {
    "quality": {
        "name": "AI Quality & Evaluation",
        "patterns": [
            r"\bquality\b",
            r"\bevaluat\w*\b",        # evaluate, evaluation, evaluator
            r"\bqa\b",                # QA, qa engineer
            r"\bbenchmark\w*\b",
            r"\bguardrail\w*\b",
            r"\balignment\b",
            r"\bred[\s-]?team\w*\b",
            r"\bragas\b",
            r"\btesting\b.*\bai\b",
            r"\bai\b.*\btest\w*\b",
        ],
    },
    "auditor": {
        "name": "AI Auditor & Governance",
        "patterns": [
            r"\baudit\w*\b",
            r"\bcompliance\b",
            r"\bgovernance\b",
            r"\bresponsible\b.*\bai\b",
            r"\bai\b.*\bresponsible\b",
            r"\btrustworthy\b",
            r"\bsafety\b.*\bai\b",
            r"\bai\b.*\bsafety\b",
            r"\bethic\w+\b",
            r"\brisk\b.*\bai\b",
            r"\bai\b.*\brisk\b",
            r"\bregulat\w+\b.*\bai\b",
            r"\bai\b.*\bregulat\w+\b",
        ],
    },
    "pipeline": {
        "name": "Data Pipeline & Annotation",
        "patterns": [
            r"\bdata\s+pipeline\b",
            r"\betl\b",
            r"\bdataflow\b",
            r"\beingestion\b",
            r"\blabel\w*\b",          # label, labeling, labels
            r"\bannotation\b",
            r"\bsynthetic\s+data\b",
            r"\bdataset\w*\b",
            r"\bfine[\s-]?tun\w*\b",
            r"\brlhf\b",
            r"\breinforcement\b.*\blearning\b",
        ],
    },
    "prompt": {
        "name": "Prompt Engineering",
        "patterns": [
            r"\bprompt\s+engineer\w*\b",
            r"\bprompt\s+design\w*\b",
            r"\bprompt\s+architec\w*\b",
        ],
    },
    "engineering": {
        "name": "AI Engineering & Applied ML",
        "patterns": [
            r"\bai\s+engineer\w*\b",
            r"\bml\s+engineer\w*\b",
            r"\bmachine\s+learning\s+engineer\w*\b",
            r"\bapplied\s+ai\b",
            r"\bapplied\s+ml\b",
            r"\bllm\s+engineer\w*\b",
            r"\bgenerative\s+ai\s+engineer\w*\b",
            r"\bgenerative\s+ai\s+research\w*\b",
            r"\bmlops\b",
            # Tooling / research eng variants
            r"\bai\s+tooling\b",
            r"\bai\s+tooling\s+engineer\w*\b",
            r"\bresearch\s+engineer\w*\b.*\bai\b",
            r"\bai\b.*\bresearch\s+engineer\w*\b",
        ],
    },
    "editor": {
        "name": "AI Editorial & Content Ops",
        "patterns": [
            r"\bai\s+editor\w*\b",
            r"\bai\s+writer\b",
            r"\bcontent\s+ops\b",
            r"\beditorial\b.*\bai\b",
            r"\bai\b.*\beditorial\b",
            r"\bai\s+content\b",
            r"\bai\s+copy\w*\b",
        ],
    },
    "gtm": {
        "name": "AI Product, Marketing & GTM",
        "patterns": [
            # Product marketing for AI tools
            r"\bproduct\s+market\w*\b.*\bai\b",
            r"\bai\b.*\bproduct\s+market\w*\b",
            # AI-specific PM roles
            r"\bai\s+product\s+manag\w*\b",
            r"\bproduct\s+manag\w*\b.*\bai\b",
            # Marketing roles with AI in tags/context (Elevenlabs, Anthropic etc)
            r"\btechnical\s+market\w*\b",
            r"\bgrowth\s+market\w*\b.*\bai\b",
            r"\bai\b.*\bgrowth\s+market\w*\b",
            # Head of Marketing / CMO at AI companies (when AI is in tags/desc)
            r"\bhead\s+of\s+market\w*\b",
            r"\bhead\s+of\s+communication\w*\b",
            # AI sales / implementation / consulting — broader (no AI in title required,
            # AI context comes from description via AI-gate pass)
            r"\baccount\s+executive\b",
            r"\bstrategic\s+account\b",
            r"\bbusiness\s+development\b",
            r"\bsolutions?\s+consult\w*\b",
            r"\bpre[\s-]?sales\b",
            r"\bimplementation\b",
            r"\bsales\s+engineer\w*\b",
            # RevOps at AI companies
            r"\brevenue\s+operations?\b",
            # Scientific/clinical roles for AI companies (Ataraxis, etc.)
            r"\bscientific\s+engagement\b",
            r"\bclinical\s+ai\b",
        ],
    },
}

# Negative filters — job titles that mention AI in name but aren't actually AI roles
NEG_PATTERNS = [
    r"\bremote\s+ai\s+assistant\b",   # generic "remote AI assistant" (admin)
    r"\bvirtual\s+assistant\b",
    r"\bai\s+prompt\s+engineer\s+needed\s+urgently\b",  # spam pattern
]

# AI gate — title or tags must contain AI/ML/LLM
AI_GATE = re.compile(
    r"\b(ai|ml|llm|gpt|claude|gemini|machine\s+learning|deep\s+learning|"
    r"neural|nlp|generative|chatbot|fine[\s-]?tun\w*|transformer\w*|"
    r"rag|vector\s+db|embedding\w*|prompt|llms?)\b",
    re.IGNORECASE,
)


def _matches_any(text, patterns):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def passes_ai_gate(job):
    """Title or tags must mention AI/ML/LLM. Description may also include AI.
    v2.1: title or tags give a 'must have' AI hint; description can also qualify
    but only if at least one of the title-tags has an adjacent AI context word
    OR description mentions AI/ML/LLM at all.
    """
    title_and_tags = job["title"] + " " + " ".join(job["tags"])
    full = title_and_tags + " " + job.get("desc", "")
    if AI_GATE.search(title_and_tags):
        return True
    # If description mentions AI 2+ times, allow it (likely an AI role)
    desc_hits = len(AI_GATE.findall(job.get("desc", "")))
    return desc_hits >= 2


def is_negative(job):
    """Filter out obvious non-AI roles that happen to mention AI."""
    title = job["title"]
    return any(re.search(p, title, re.IGNORECASE) for p in NEG_PATTERNS)


def categorize(job):
    """Return primary category by title+tags keyword match.

    v2.3.1: also scan description when AI-gate passes via description (2+ AI mentions),
    so titles that don't name the role type but are clearly AI-context roles get categorized.
    v2.3: 7 categories now (added 'gtm' for Product/Marketing/Sales/Consulting
    roles at AI companies).
    Priority order: quality > auditor > pipeline > prompt > engineering > editor > gtm
    """
    title_and_tags = job["title"] + " " + " ".join(job["tags"])
    desc = job.get("desc", "")
    # If the AI gate passed via description (2+ AI hits), include desc in matching
    desc_hits = len(AI_GATE.findall(desc))
    scan_text = title_and_tags + " " + desc if desc_hits >= 2 else title_and_tags
    matches = []
    for cat in ["quality", "auditor", "pipeline", "prompt", "engineering", "editor", "gtm"]:
        if _matches_any(scan_text, CATS[cat]["patterns"]):
            matches.append(cat)
    return matches[0] if matches else None


# ---------- DIGEST BUILD ----------

def dedupe(jobs):
    seen, out = set(), []
    for j in jobs:
        key = (j["title"].lower(), j["company"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def build_digest(jobs, since_days=7):
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=since_days)
    fresh = [j for j in jobs if j["posted"] >= cutoff]
    fresh.sort(key=lambda j: j["posted"], reverse=True)

    # Filter: must pass AI gate, must not be negative
    ai_jobs = [j for j in fresh if passes_ai_gate(j) and not is_negative(j)]

    by_cat = {cat: [] for cat in CATS}
    categorized = []
    for j in ai_jobs:
        cat = categorize(j)
        if cat:
            by_cat[cat].append(j)
            categorized.append(j)

    uncategorized = [j for j in ai_jobs if not categorize(j)]

    top = {cat: jobs[:5] for cat, jobs in by_cat.items()}
    # GTM is broader and lower-priority — top 3 only to save real estate
    if "gtm" in top:
        top["gtm"] = top["gtm"][:3]
    return {
        "fresh": fresh,
        "categorized": categorized,
        "uncategorized": uncategorized,
        "by_cat": by_cat,
        "top": top,
        "total_fresh": len(fresh),
        "total_ai": len(ai_jobs),
        "total_categorized": len(categorized),
        "cutoff": cutoff,
    }


# ---------- RENDER ----------

def render_md(d, today):
    lines = []
    lines.append(f"# AI Quality Jobs — Weekly Digest ({today.isoformat()})")
    lines.append("")
    lines.append(f"**{d['total_categorized']} matched roles** out of {d['total_fresh']} fresh postings (last 7 days).")
    lines.append(f"AI-gate filter + 7-category classifier (v2.3.1: quality/auditor/pipeline/prompt/engineering/editor/gtm).")
    lines.append("")
    lines.append("---")
    lines.append("")
    for cat, jobs in d["top"].items():
        if not jobs:
            continue
        lines.append(f"## {CATS[cat]['name']} ({len(jobs)})")
        lines.append("")
        for j in jobs:
            tag_str = ", ".join(t for t in j["tags"] if t)[:120]
            lines.append(f"### [{escape(j['title'])}]({j['url']})")
            lines.append(f"**{escape(j['company'])}** · {j['posted'].isoformat()} · {j['src']}")
            if tag_str:
                lines.append(f"Tags: `{tag_str}`")
            lines.append("")
    if d["uncategorized"]:
        lines.append(f"## Other AI roles ({len(d['uncategorized'])})")
        lines.append("")
        for j in d["uncategorized"][:5]:
            lines.append(f"- [{escape(j['title'])}]({j['url']}) — {escape(j['company'])} · {j['posted'].isoformat()}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Browse all open roles →** https://aldow3n-a11y.github.io/ai-quality-jobs/")
    lines.append("")
    lines.append("**Advertise with us** — feature your AI role or product to this audience for $199/30d → https://aldow3n-a11y.github.io/ai-quality-jobs/advertise.html")
    lines.append("")
    lines.append("You're getting this because you subscribed at the AI Quality Jobs board.")
    lines.append("Reply with 'pause' to skip a week, 'unsub' to stop.")
    lines.append("")
    return "\n".join(lines)


def render_html(d, today):
    parts = []
    parts.append('<!doctype html><html><body style="font-family:Inter,Arial,sans-serif;color:#1A1814;background:#F8F4EE;margin:0;padding:24px">')
    parts.append('<div style="max-width:680px;margin:0 auto;background:#fff;padding:32px;border:1px solid #D9D2BF">')
    parts.append('<h1 style="font-family:Georgia,serif;font-weight:500;margin:0 0 8px 0">AI Quality Jobs — Weekly Digest</h1>')
    parts.append(f'<p style="font-family:monospace;font-size:12px;color:#6B6557;margin:0 0 24px 0">{today.isoformat()} · {d["total_categorized"]} matched · {d["total_fresh"]} fresh</p>')
    parts.append('<hr style="border:none;border-top:1px solid #D9D2BF;margin:24px 0">')
    for cat, jobs in d["top"].items():
        if not jobs:
            continue
        parts.append(f'<h2 style="font-family:Georgia,serif;font-weight:500;color:#1F3D2E;border-bottom:1px solid #D9D2BF;padding-bottom:8px">{CATS[cat]["name"]} ({len(jobs)})</h2>')
        for j in jobs:
            parts.append('<div style="margin:16px 0;padding:12px;border-left:3px solid #C2410C;background:#F8F4EE">')
            parts.append(f'<a href="{escape(j["url"])}" style="color:#1F3D2E;font-weight:600;font-size:16px;text-decoration:none">{escape(j["title"])}</a>')
            parts.append(f'<div style="font-family:monospace;font-size:11px;color:#6B6557;margin-top:4px">{escape(j["company"])} · {j["posted"].isoformat()} · {j["src"]}</div>')
            parts.append("</div>")
    parts.append('<hr style="border:none;border-top:1px solid #D9D2BF;margin:24px 0">')
    parts.append('<p><a href="https://aldow3n-a11y.github.io/ai-quality-jobs/" style="color:#C2410C">Browse all open roles →</a></p>')
    parts.append('<p style="font-size:12px;color:#6B6557">★ <a href="https://aldow3n-a11y.github.io/ai-quality-jobs/advertise.html" style="color:#C2410C">Advertise with AI Quality Jobs</a> — feature your AI role to this subscriber list from $199/30d.</p>')
    parts.append('<p style="font-size:12px;color:#6B6557">Reply with \'pause\' to skip a week, \'unsub\' to stop.</p>')
    parts.append("</div></body></html>")
    return "".join(parts)


def render_txt(d, today):
    parts = []
    parts.append(f"AI QUALITY JOBS — WEEKLY DIGEST ({today.isoformat()})")
    parts.append(f"{d['total_categorized']} matched of {d['total_fresh']} fresh postings")
    parts.append("=" * 60)
    for cat, jobs in d["top"].items():
        if not jobs:
            continue
        parts.append("")
        parts.append(CATS[cat]["name"] + f" ({len(jobs)})")
        parts.append("-" * 40)
        for j in jobs:
            parts.append(f"• {j['title']}")
            parts.append(f"  {j['company']} · {j['posted'].isoformat()} · {j['src']}")
            parts.append(f"  {j['url']}")
            parts.append("")
    if d["uncategorized"]:
        parts.append("")
        parts.append(f"OTHER AI ROLES ({len(d['uncategorized'])})")
        parts.append("-" * 40)
        for j in d["uncategorized"][:5]:
            parts.append(f"• {j['title']}")
            parts.append(f"  {j['company']} · {j['posted'].isoformat()}")
            parts.append("")
    parts.append("=" * 60)
    parts.append("Browse all: https://aldow3n-a11y.github.io/ai-quality-jobs/")
    parts.append("Advertise: https://aldow3n-a11y.github.io/ai-quality-jobs/advertise.html ($199/30d featured slot)")
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
    print(f"  {d['total_ai']} pass AI gate", file=sys.stderr)
    print(f"  {d['total_categorized']} categorized", file=sys.stderr)
    for cat, lst in d["by_cat"].items():
        print(f"    {cat}: {len(lst)}", file=sys.stderr)
    print(f"  {len(d['uncategorized'])} uncategorized AI roles", file=sys.stderr)

    md_path = out_dir / f"digest-{today.isoformat()}.md"
    html_path = out_dir / f"digest-{today.isoformat()}.html"
    txt_path = out_dir / f"digest-{today.isoformat()}.txt"

    md_path.write_text(render_md(d, today), encoding="utf-8")
    html_path.write_text(render_html(d, today), encoding="utf-8")
    txt_path.write_text(render_txt(d, today), encoding="utf-8")

    print(f"Wrote: {md_path.name}, {html_path.name}, {txt_path.name}", file=sys.stderr)
    print("\n=== LATEST DIGEST PREVIEW (markdown, first 30 lines) ===\n")
    print("\n".join(render_md(d, today).splitlines()[:30]))


if __name__ == "__main__":
    main()
