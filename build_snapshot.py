#!/usr/bin/env python3
"""
AI Quality Jobs — build-time snapshot generator.

Pulls jobs from all 3 APIs once at build time, generates:
  1. /jobs/index.html          — full searchable list
  2. /jobs/{slug}.html         — one per job (SEO long-tail)
  3. /sitemap.xml              — for Google Search Console
  4. /rss.xml                  — RSS feed for subscribers/aggregators
  5. /jobs/{category}.html     — category landing pages (LLM Quality, AI Auditor, etc.)
  6. /data/jobs.json           — full normalized data, served to live JS

Run:
  python3 build_snapshot.py           # builds everything
  python3 build_snapshot.py --stats    # just print counts, no writes
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html import escape

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_ORIGIN = "https://aldow3n-a11y.github.io"
SITE_PATH = "/ai-quality-jobs"

SOURCES = [
    {
        "name": "RemoteOK",
        "short": "ROK",
        "url": "https://www.remoteok.com/api?tag=ai",
        "wrap": "raw",
        "map": lambda j: {
            "title":   j.get("position", ""),
            "company": j.get("company", ""),
            "tags":    j.get("tags", []) or [],
            "url":     j.get("url") or (f"https://remoteok.com/remote-jobs/{j['slug']}" if j.get("slug") else ""),
            "date":    j.get("date", ""),
            "desc":    strip_html(j.get("description", "")),
            "raw_desc": j.get("description", ""),
        }
    },
    {
        "name": "Remotive",
        "short": "REM",
        "url": "https://remotive.com/api/remote-jobs?category=software-dev",
        "wrap": "jobs",
        "map": lambda j: {
            "title":   j.get("title", ""),
            "company": j.get("company_name", ""),
            "tags":    j.get("tags", []) or [],
            "url":     j.get("url", ""),
            "date":    j.get("publication_date", ""),
            "desc":    strip_html(j.get("description", "")),
            "raw_desc": j.get("description", ""),
        }
    },
    {
        "name": "Jobicy",
        "short": "JOB",
        "url": "https://jobicy.com/api/v2/remote-jobs?count=50",
        "wrap": "jobs",
        "map": lambda j: {
            "title":   j.get("jobTitle", ""),
            "company": j.get("companyName", ""),
            "tags":    j.get("jobIndustry", []) if isinstance(j.get("jobIndustry"), list) else [j.get("jobIndustry", "")],
            "url":     j.get("url", ""),
            "date":    j.get("pubDate", ""),
            "desc":    strip_html(j.get("jobExcerpt", "")),
            "raw_desc": j.get("description", j.get("jobExcerpt", "")),
        }
    },
]

AI_KEYWORDS = {
    "auditor":  ["auditor", "audit", "compliance", "governance", "risk", "eu ai act", "safety", "red team", "red-team", "eval", "evaluation", "bias"],
    "quality":  ["quality", "qa", "analyst", "evaluation", "regression", "test", "benchmark", "annotator", "reviewer"],
    "pipeline": ["engineer", "pipeline", "agent", "orchestration", "llm", "rag", "langchain", "llamaindex", "mlops", "platform", "architect"],
    "editor":   ["editor", "content", "copywriter", "writer", "fact-check", "fact check", "curator", "copy"],
}

CATEGORIES = {
    "auditor":  {"label": "AI Auditor",       "desc": "Safety, compliance, governance, red-team, EU AI Act."},
    "quality":  {"label": "LLM Quality",      "desc": "Evaluation, regression testing, QA, annotation, benchmarking."},
    "pipeline": {"label": "AI Pipeline",      "desc": "LLM infra, RAG, orchestration, agents, MLOps, platforms."},
    "editor":   {"label": "AI Editor",        "desc": "Output review, fact-check, content curation, copywriting."},
}


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:280]


def slugify(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:80] or "job"


def classify(job):
    hay = (job["title"] + " " + " ".join(job["tags"]) + " " + job["desc"]).lower()
    scores = {c: sum(1 for k in kw if k in hay) for c, kw in AI_KEYWORDS.items()}
    best, best_n = "pipeline", 0
    for c, n in scores.items():
        if n > best_n:
            best, best_n = c, n
    return best


def fetch_source(src):
    try:
        req = urllib.request.Request(src["url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", errors="ignore"))
        arr = data if src["wrap"] == "raw" else data.get("jobs", [])
        jobs = []
        for i, raw in enumerate(arr[:100]):
            try:
                norm = src["map"](raw)
            except Exception:
                continue
            if not norm["title"]:
                continue
            norm["source"] = src["name"]
            norm["source_short"] = src["short"]
            norm["source_index"] = i
            jobs.append(norm)
        return jobs
    except Exception as e:
        sys.stderr.write(f"[warn] {src['name']}: {e}\n")
        return []


def load_all():
    all_jobs = []
    seen_urls = set()
    for src in SOURCES:
        for j in fetch_source(src):
            url = j.get("url", "")
            key = (j["title"].lower(), j["company"].lower())
            # simple dedupe on (title,company)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            j["category"] = classify(j)
            all_jobs.append(j)
    # stable sort: jobs with dates first (newest), then by recency of date
    def sortkey(j):
        t = 0
        if j["date"]:
            try:
                t = datetime.fromisoformat(j["date"].replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
        return (t, j["source"])
    all_jobs.sort(key=sortkey, reverse=True)
    # assign slug + id
    used = set()
    for i, j in enumerate(all_jobs):
        base = slugify(f"{j['title']}-{j['company']}")
        s = base
        n = 2
        while s in used:
            s = f"{base}-{n}"
            n += 1
        used.add(s)
        j["slug"] = s
        j["id"] = f"{j['source_short'].lower()}-{i:03d}-{s}"
    return all_jobs


# ------------------- HTML generators -------------------

def page_shell(title, description, body, canonical_path):
    """Wrap content in the same editorial layout as index.html."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<link rel="canonical" href="{SITE_ORIGIN}{canonical_path}">
<meta property="og:title" content="{escape(title)}">
<meta property="og:description" content="{escape(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{SITE_ORIGIN}{canonical_path}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%231F3D2E'/%3E%3Ctext x='32' y='42' font-family='Georgia' font-size='34' font-weight='700' text-anchor='middle' fill='%23F8F4EE'%3EAq%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,300..900,0..100&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#F8F4EE;--bg-2:#EFE9DD;--ink:#1A1814;--ink-2:#3A352C;--muted:#6B6557;
  --border:#D9D2BF;--accent:#1F3D2E;--accent-2:#C2410C;--highlight:#F1E9D2;
  --font-display:"Fraunces",Georgia,serif;--font-body:Inter,-apple-system,sans-serif;
  --font-mono:"JetBrains Mono",ui-monospace,monospace;
  --max:1180px;--gutter:clamp(20px,4vw,56px);--radius:6px;
}}
*,*::before,*::after{{box-sizing:border-box}}html,body{{margin:0;padding:0}}
body{{background:var(--bg);color:var(--ink);font-family:var(--font-body);font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}}
a{{color:inherit}}.nav{{border-bottom:1px solid var(--border);background:var(--bg);position:sticky;top:0;z-index:10}}
.nav-inner{{max-width:var(--max);margin:0 auto;padding:14px var(--gutter);display:flex;align-items:center;justify-content:space-between;gap:24px}}
.brand{{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink);font-family:var(--font-display);font-weight:700;font-size:22px;letter-spacing:-0.01em}}
.brand-mark{{width:28px;height:28px;border-radius:7px;background:var(--accent);color:var(--bg);display:grid;place-items:center;font-family:var(--font-display);font-weight:700;font-size:15px}}
.brand-mark::after{{content:"Aq"}}.nav-links{{display:flex;gap:22px;font-size:14px;color:var(--ink-2);align-items:center}}
.nav-links a{{text-decoration:none;color:var(--ink-2);font-weight:500;border-bottom:1px solid transparent;padding-bottom:1px;transition:border-color 0.2s}}
.nav-links a:hover{{border-bottom-color:var(--accent-2)}}
.btn-post{{background:var(--accent);color:var(--bg);padding:8px 14px;border-radius:var(--radius);text-decoration:none;font-weight:600;font-size:14px;border:none}}
.btn-post:hover{{background:#16301F}}
@media(max-width:720px){{.nav-links a:not(.btn-post){{display:none}}}}
.container{{max-width:var(--max);margin:0 auto;padding:48px var(--gutter)}}
.eyebrow{{font-family:var(--font-mono);font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:var(--accent-2);margin-bottom:18px}}
h1.title{{font-family:var(--font-display);font-weight:500;font-size:clamp(34px,5vw,56px);line-height:1.04;letter-spacing:-0.025em;margin:0 0 16px 0;color:var(--ink);font-variation-settings:"opsz" 144,"SOFT" 30}}
h2{{font-family:var(--font-display);font-weight:500;font-size:clamp(24px,3vw,32px);line-height:1.15;letter-spacing:-0.02em;margin:48px 0 16px 0;font-variation-settings:"opsz" 144,"SOFT" 50}}
p.lead{{font-size:18px;color:var(--ink-2);max-width:60ch;margin:0 0 24px 0}}
.crumbs{{font-family:var(--font-mono);font-size:12px;color:var(--muted);margin-bottom:24px;letter-spacing:0.04em}}
.crumbs a{{color:var(--muted);text-decoration:none;border-bottom:1px solid var(--border)}}
.meta-bar{{display:flex;flex-wrap:wrap;gap:14px 24px;padding:14px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin:24px 0;font-family:var(--font-mono);font-size:13px;color:var(--muted);letter-spacing:0.04em}}
.meta-bar .pill{{background:var(--highlight);color:var(--ink-2);padding:3px 9px;border-radius:var(--radius);font-size:11px;text-transform:uppercase;letter-spacing:0.08em}}
.tag-row{{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}}
.tag{{font-family:var(--font-mono);font-size:11px;letter-spacing:0.06em;color:var(--muted);background:var(--bg-2);padding:4px 9px;border-radius:var(--radius);border:1px solid var(--border)}}
.desc{{font-size:17px;line-height:1.6;color:var(--ink-2);margin:24px 0;max-width:65ch;white-space:pre-wrap}}
.apply-box{{margin:32px 0;padding:20px;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg-2);display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}}
.apply-btn{{background:var(--accent-2);color:#fff;padding:14px 28px;border-radius:var(--radius);text-decoration:none;font-weight:600;font-size:16px;display:inline-block}}
.apply-btn:hover{{background:#9E3308}}
.related-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;margin:24px 0}}
.related-card{{display:block;padding:18px;border:1px solid var(--border);border-radius:var(--radius);text-decoration:none;color:inherit;transition:border-color 0.15s,transform 0.15s}}
.related-card:hover{{border-color:var(--accent-2);transform:translateY(-2px)}}
.related-card h3{{font-family:var(--font-display);font-weight:500;font-size:18px;margin:0 0 6px 0;line-height:1.25}}
.related-card .co{{font-size:13px;color:var(--muted);font-family:var(--font-mono);letter-spacing:0.04em}}
.footer{{border-top:1px solid var(--border);padding:32px var(--gutter);margin-top:64px;font-family:var(--font-mono);font-size:12px;color:var(--muted);letter-spacing:0.04em}}
.footer-inner{{max-width:var(--max);margin:0 auto;display:flex;justify-content:space-between;flex-wrap:wrap;gap:16px}}
.footer a{{color:var(--ink-2);text-decoration:none;border-bottom:1px solid var(--border)}}
.job-list{{list-style:none;padding:0;margin:0;display:grid;gap:1px;background:var(--border);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}}
.job-list li{{background:var(--bg)}}
.job-list a{{display:grid;grid-template-columns:auto 1fr auto;gap:16px;align-items:start;padding:18px var(--gutter);text-decoration:none;color:inherit}}
.job-list a:hover{{background:var(--bg-2)}}
.job-list .job-title{{font-family:var(--font-display);font-weight:500;font-size:18px;margin:0 0 4px 0;line-height:1.2}}
.job-list .co{{font-size:13px;color:var(--muted)}}
.job-list .date{{font-family:var(--font-mono);font-size:11px;color:var(--muted);text-align:right;white-space:nowrap}}
.filter-bar{{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0;padding-bottom:16px;border-bottom:1px solid var(--border)}}
.filter-bar a{{font-family:var(--font-mono);font-size:11px;letter-spacing:0.08em;text-transform:uppercase;padding:7px 12px;border:1px solid var(--border);border-radius:var(--radius);text-decoration:none;color:var(--ink-2)}}
.filter-bar a:hover{{border-color:var(--accent-2)}}
</style>
</head>
<body>
<nav class="nav">
<div class="nav-inner">
<a class="brand" href="{SITE_PATH}/"><span class="brand-mark"></span><span>AI Quality Jobs</span></a>
<div class="nav-links">
<a href="{SITE_PATH}/">Board</a>
<a href="{SITE_PATH}/jobs/">All jobs</a>
<a href="{SITE_PATH}/rss.xml">RSS</a>
<a class="btn-post" href="{SITE_PATH}/#post">Post a role</a>
</div>
</div>
</nav>
{body}
<footer class="footer">
<div class="footer-inner">
<div>AI Quality Jobs · aggregated from <a href="https://remoteok.com">RemoteOK</a>, <a href="https://remotive.com">Remotive</a>, <a href="https://jobicy.com">Jobicy</a></div>
<div><a href="{SITE_PATH}/sitemap.xml">sitemap</a> · <a href="{SITE_PATH}/rss.xml">rss</a></div>
</div>
</footer>
</body>
</html>
"""


def render_job_page(j):
    cat = CATEGORIES.get(j["category"], {"label": j["category"]})
    tags_html = "".join(f'<span class="tag">{escape(t)}</span>' for t in (j["tags"] or [])[:8])
    try:
        date_str = datetime.fromisoformat(j["date"].replace("Z", "+00:00")).strftime("%B %d, %Y")
    except Exception:
        date_str = j["date"] or "Date unknown"

    body = f"""
<div class="container">
<div class="crumbs"><a href="{SITE_PATH}/">Board</a> / <a href="{SITE_PATH}/jobs/c/{j['category']}.html">{escape(cat['label'])}</a> / <span>{escape(j['company'])}</span></div>
<div class="eyebrow">{escape(cat['label'])} · via {escape(j['source'])}</div>
<h1 class="title">{escape(j['title'])}</h1>
<p class="lead"><strong>{escape(j['company'])}</strong> is hiring for this role. Apply directly via the source link below — listings are auto-aggregated and not edited by us.</p>
<div class="meta-bar">
<span>Posted: <strong>{escape(date_str)}</strong></span>
<span>Source: <strong>{escape(j['source'])}</strong></span>
<span>Category: <span class="pill">{escape(cat['label'])}</span></span>
</div>
<div class="tag-row">{tags_html}</div>
<div class="desc">{escape(j['desc']) if j['desc'] else 'No description snippet provided by the source. Click apply to read the full role on the source site.'}</div>
<div class="apply-box">
<div>
<div style="font-family:var(--font-mono);font-size:12px;color:var(--muted);letter-spacing:0.04em;margin-bottom:4px">READY TO APPLY?</div>
<div>Opens the original posting on {escape(j['source'])}.</div>
</div>
<a class="apply-btn" href="{escape(j['url'])}" target="_blank" rel="noopener nofollow">Apply now →</a>
</div>
<h2>About this category</h2>
<p>{escape(cat.get('desc', ''))}</p>
<h2>More {escape(cat['label'])} roles</h2>
<div class="related-grid">
{{RELATED}}
</div>
</div>
"""
    return body, date_str


def render_job_index(jobs):
    items = []
    for j in jobs:
        try:
            d = datetime.fromisoformat(j["date"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except Exception:
            d = j["date"][:10] if j["date"] else "—"
        items.append(f"""<li>
<a href="{SITE_PATH}/jobs/{escape(j['slug'])}.html">
<div style="font-family:var(--font-mono);font-size:11px;color:var(--muted);padding-top:4px;min-width:30px">{escape(CATEGORIES.get(j['category'], {}).get('label', '')[:3].upper())}</div>
<div>
<h3 class="job-title">{escape(j['title'])}</h3>
<div class="co">{escape(j['company'])} · {escape(j['source'])}</div>
</div>
<div class="date">{d}</div>
</a>
</li>""")
    return "\n".join(items)


def render_category_page(jobs, cat_key):
    cat_jobs = [j for j in jobs if j["category"] == cat_key]
    info = CATEGORIES[cat_key]
    body = f"""
<div class="container">
<div class="crumbs"><a href="{SITE_PATH}/">Board</a> / <a href="{SITE_PATH}/jobs/">All jobs</a> / <span>{escape(info['label'])}</span></div>
<div class="eyebrow">Category · {len(cat_jobs)} live roles</div>
<h1 class="title">{escape(info['label'])} jobs</h1>
<p class="lead">{escape(info['desc'])}</p>
<div class="filter-bar">
<a href="{SITE_PATH}/jobs/">All</a>
""" + "".join(f'<a href="{SITE_PATH}/jobs/c/{k}.html">{escape(CATEGORIES[k]["label"])}</a>' for k in CATEGORIES) + f"""
</div>
<ul class="job-list">
{render_job_index(cat_jobs)}
</ul>
</div>
"""
    return body


def render_jobs_landing(jobs):
    by_cat = {c: [j for j in jobs if j["category"] == c] for c in CATEGORIES}
    body = f"""
<div class="container">
<div class="crumbs"><a href="{SITE_PATH}/">Board</a> / <span>All jobs</span></div>
<div class="eyebrow">Index · {len(jobs)} roles across 3 sources</div>
<h1 class="title">All AI quality roles</h1>
<p class="lead">A flat index of every role that matched our quality-filter on the most recent crawl. Sorted newest first. Each card links to the original posting.</p>
<div class="filter-bar">
<a href="{SITE_PATH}/jobs/">All ({len(jobs)})</a>
""" + "".join(f'<a href="{SITE_PATH}/jobs/c/{k}.html">{escape(CATEGORIES[k]["label"])} ({len(v)})</a>' for k, v in by_cat.items()) + f"""
</div>
<ul class="job-list">
{render_job_index(jobs)}
</ul>
</div>
"""
    return body


def render_sitemap(jobs):
    urls = [f"{SITE_ORIGIN}{SITE_PATH}/", f"{SITE_ORIGIN}{SITE_PATH}/jobs/"]
    urls += [f"{SITE_ORIGIN}{SITE_PATH}/jobs/c/{c}.html" for c in CATEGORIES]
    for j in jobs:
        urls.append(f"{SITE_ORIGIN}{SITE_PATH}/jobs/{j['slug']}.html")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = "\n".join(
        f"""  <url>
    <loc>{u}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.7</priority>
  </url>""" for u in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}
</urlset>
"""


def render_rss(jobs):
    items = []
    for j in jobs[:50]:
        try:
            dt = datetime.fromisoformat(j["date"].replace("Z", "+00:00")).strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception:
            dt = ""
        items.append(f"""    <item>
      <title>{escape(j['title'])} at {escape(j['company'])}</title>
      <link>{escape(j['url'])}</link>
      <guid isPermaLink="false">{escape(j['id'])}</guid>
      <pubDate>{dt}</pubDate>
      <category>{escape(CATEGORIES.get(j['category'], {}).get('label', ''))}</category>
      <description>{escape(j['desc'])}</description>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>AI Quality Jobs</title>
  <link>{SITE_ORIGIN}{SITE_PATH}/</link>
  <description>Curated remote AI systems, eval, and quality roles.</description>
  <language>en-us</language>
  <lastBuildDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>
{chr(10).join(items)}
</channel>
</rss>
"""


def main():
    stats_only = "--stats" in sys.argv
    jobs = load_all()
    print(f"Loaded {len(jobs)} jobs from {len(SOURCES)} sources")
    if stats_only:
        by_cat = {}
        for j in jobs:
            by_cat[j["category"]] = by_cat.get(j["category"], 0) + 1
        for c, n in by_cat.items():
            print(f"  {CATEGORIES[c]['label']}: {n}")
        return

    # write data JSON for live JS
    data_path = os.path.join(OUT_DIR, "data", "jobs.json")
    os.makedirs(os.path.dirname(data_path), exist_ok=True)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "count": len(jobs), "jobs": jobs}, f, indent=2)
    print(f"Wrote data/jobs.json ({os.path.getsize(data_path):,} bytes)")

    # jobs landing
    jobs_dir = os.path.join(OUT_DIR, "jobs")
    os.makedirs(jobs_dir, exist_ok=True)

    landing_path = os.path.join(jobs_dir, "index.html")
    landing_body = render_jobs_landing(jobs)
    with open(landing_path, "w", encoding="utf-8") as f:
        f.write(page_shell("All AI Quality Jobs — board index", f"Every AI systems, eval, and quality role currently indexed. {len(jobs)} live listings.", landing_body, f"{SITE_PATH}/jobs/"))
    print(f"Wrote jobs/index.html")

    # category pages
    cat_dir = os.path.join(jobs_dir, "c")
    os.makedirs(cat_dir, exist_ok=True)
    for cat_key, info in CATEGORIES.items():
        cat_body = render_category_page(jobs, cat_key)
        out = os.path.join(cat_dir, f"{cat_key}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(page_shell(f"{info['label']} jobs — AI Quality Jobs board", f"{info['desc']} {sum(1 for j in jobs if j['category']==cat_key)} live roles.", cat_body, f"{SITE_PATH}/jobs/c/{cat_key}.html"))
    print(f"Wrote {len(CATEGORIES)} category pages")

    # individual job pages — with related-card section
    by_cat = {c: [j for j in jobs if j["category"] == c] for c in CATEGORIES}
    for j in jobs:
        body, _ = render_job_page(j)
        related = []
        for r in [x for x in by_cat[j["category"]] if x["id"] != j["id"]][:6]:
            related.append(f'<a class="related-card" href="{SITE_PATH}/jobs/{escape(r["slug"])}.html"><h3>{escape(r["title"])}</h3><div class="co">{escape(r["company"])}</div></a>')
        body = body.replace("{RELATED}", "\n".join(related) if related else "<p>No related roles in this category yet — check back soon.</p>")
        out = os.path.join(jobs_dir, f"{j['slug']}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(page_shell(f"{j['title']} at {j['company']} — AI Quality Jobs", f"{j['title']} at {j['company']}. {CATEGORIES[j['category']]['label']} role. Apply via {j['source']}.", body, f"{SITE_PATH}/jobs/{j['slug']}.html"))
    print(f"Wrote {len(jobs)} individual job pages")

    # sitemap + RSS
    with open(os.path.join(OUT_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(render_sitemap(jobs))
    print(f"Wrote sitemap.xml ({len(jobs)+len(CATEGORIES)+2} URLs)")

    with open(os.path.join(OUT_DIR, "rss.xml"), "w", encoding="utf-8") as f:
        f.write(render_rss(jobs))
    print(f"Wrote rss.xml ({min(len(jobs),50)} items)")

    # robots.txt
    with open(os.path.join(OUT_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_ORIGIN}{SITE_PATH}/sitemap.xml\n")
    print(f"Wrote robots.txt")

    print(f"\nDONE. {len(jobs)} jobs baked into static pages.")


if __name__ == "__main__":
    main()
