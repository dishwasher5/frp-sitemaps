#!/usr/bin/env python3
"""Build thin-page-free sitemaps for findroofingpros.com.

Candidate URLs come from Brilliant Directories' own master sitemaps. Each
page is fetched and listed only if it returns 200, is indexable, is
self-canonical and shows at least `min_contractors` listings (read from the
page's microdata numberOfItems). Verdicts live in state/checks.json, so each
run only re-checks what is due. A guard refuses to publish any sitemap that
shrinks sharply, and nothing is published if the site starts blocking us.

Standard library only (Python 3.11+).

Usage:
  python build_sitemaps.py                 # normal scheduled run
  python build_sitemaps.py --full          # re-check every candidate now
  python build_sitemaps.py --accept-drop   # publish even if the guard trips
  python build_sitemaps.py --sample 100    # test: check 100 random URLs per
                                           # sitemap, print results, write nothing
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "state", "checks.json")
OUT_DIR = os.path.join(ROOT, "output")
ALERT_PATH = os.path.join(OUT_DIR, "ALERT.md")

LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
META_RE = re.compile(r"<meta\b[^>]*>", re.I)
LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""([a-zA-Z_:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# JSON-LD dateModified (blog posts), W3C datetime as the sitemap lastmod expects
MODIFIED_RE = re.compile(r'"dateModified"\s*:\s*"(\d{4}-\d{2}-\d{2}(?:T[0-9:.]+(?:Z|[+-]\d{2}:\d{2}))?)"')

TRANSIENT = "transient"


# ── HTTP ────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is a verdict ("http-301"), not something to follow


_OPENER = urllib.request.build_opener(_NoRedirect)


class RateLimiter:
    def __init__(self, per_second):
        self.interval = 1.0 / per_second
        self.lock = threading.Lock()
        self.next_slot = time.monotonic()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot)
            self.next_slot = slot + self.interval
        time.sleep(max(0.0, slot - now))


def fetch(url, cfg, limiter=None):
    """Return (status, headers, body). status is None on network errors."""
    if limiter:
        limiter.wait()
    req = urllib.request.Request(url, headers={
        "User-Agent": cfg["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml",
    })
    try:
        with _OPENER.open(req, timeout=cfg["timeout_seconds"]) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, body
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, ""
    except Exception:
        return None, {}, ""


# ── Page checks ─────────────────────────────────────────────────────────

def _attrs(tag):
    return {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in ATTR_RE.finditer(tag)}


def _norm(url):
    p = urlsplit(url.strip())
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/') or '/'}"


def inspect(url, status, headers, body):
    """Extract the signals the rules need from one fetched page."""
    info = {"status": status, "noindex": False, "canonical": None, "count": None, "modified": None}
    m = MODIFIED_RE.search(body)
    if m:
        info["modified"] = m.group(1)
    if "noindex" in headers.get("x-robots-tag", "").lower():
        info["noindex"] = True
    for tag in META_RE.findall(body):
        a = _attrs(tag)
        name = (a.get("name") or "").lower()
        if name in ("robots", "googlebot") and "noindex" in (a.get("content") or "").lower():
            info["noindex"] = True
        if (a.get("itemprop") or "") == "numberOfItems":
            try:
                n = int((a.get("content") or "").strip())
                info["count"] = n if info["count"] is None else max(info["count"], n)
            except ValueError:
                pass
    for tag in LINK_RE.findall(body):
        a = _attrs(tag)
        if "canonical" in (a.get("rel") or "").lower().split() and a.get("href"):
            info["canonical"] = a["href"]
            break
    return info


def verdict(url, info, rules, min_contractors):
    """Return (passes, reason). reason is '' when the page passes."""
    s = info["status"]
    if s is None or s in (403, 429) or s >= 500:
        return None, TRANSIENT
    if s != 200:
        return False, f"http-{s}"
    if rules["require_index"] and info["noindex"]:
        return False, "noindex"
    if rules["require_self_canonical"]:
        if not info["canonical"]:
            return False, "no-canonical"
        if _norm(info["canonical"]) != _norm(url):
            return False, "canonical-elsewhere"
    if min_contractors <= 0:  # not a results page (e.g. blog posts)
        return True, ""
    if info["count"] is None:
        return False, "no-count"
    if info["count"] < min_contractors:
        return False, "thin"
    return True, ""


def check_url(url, cfg, group, limiter, deadline):
    if time.monotonic() > deadline:
        return url, None
    min_c = group.get("min_contractors", cfg["rules"]["min_contractors"])
    status, headers, body = fetch(url, cfg, limiter)
    info = inspect(url, status, headers, body)
    ok, reason = verdict(url, info, cfg["rules"], min_c)
    if reason == TRANSIENT:  # one retry after a pause before calling it transient
        time.sleep(5)
        status, headers, body = fetch(url, cfg, limiter)
        info = inspect(url, status, headers, body)
        ok, reason = verdict(url, info, cfg["rules"], min_c)
    return url, {"ok": ok, "reason": reason, "count": info["count"],
                 "status": info["status"], "noindex": info["noindex"],
                 "modified": info["modified"]}


# ── Candidates ──────────────────────────────────────────────────────────

def candidates(cfg, group):
    """All candidate URLs for a group, or None if any source can't be read."""
    result = candidate_sets(cfg, group)
    return None if result is None else sorted(result[0])


def candidate_sets(cfg, group):
    """(all candidate URLs, URLs listed directly in a plain source), or None."""
    site = cfg["site"].rstrip("/") + "/"
    urls = set()
    native = set()
    # Allow-listed sources only contribute paths named in a file (one path per
    # line): e.g. the flat /{city} URLs kept as "Which Springfield?" choosers.
    allow = None
    if group.get("allow_list"):
        with open(os.path.join(ROOT, group["allow_list"]), encoding="utf-8") as f:
            allow = {line.strip().strip("/") for line in f if line.strip()}
    plain = [(s, None) for s in group["sources"]]
    derived = ([(s, "first") for s in group.get("derive_drop_first", [])]
               + [(s, "last") for s in group.get("derive_drop_last", [])])
    allowed = [(s, "allow") for s in group.get("allow_sources", [])]
    segments = group.get("segments")
    for src, mode in plain + derived + allowed:
        status, _, body = fetch(site + src, cfg)
        locs = LOC_RE.findall(body) if status == 200 else []
        if not locs:
            print(f"  ! source unreadable or empty: {src} (status {status})")
            return None
        for loc in locs:
            path = re.sub(r"^https?://[^/]+", "", loc).strip("/")   # BD lists the homepage without a trailing slash
            parts = path.split("/")
            if mode in ("first", "last"):
                if len(parts) < 2:
                    continue
                path = "/".join(parts[1:] if mode == "first" else parts[:-1])
                if segments and len(path.split("/")) != segments:
                    continue
            elif mode == "allow":
                if allow is None or path not in allow:
                    continue
            elif segments and len(parts) != segments:
                continue
            if path or mode is None:   # an empty path is the homepage (static pages list it)
                urls.add(site + path)
                if mode in (None, "allow"):
                    native.add(site + path)
    excluded = {site + p.strip("/") for p in group.get("exclude", [])}
    return urls - excluded, native - excluded


def assign_groups(cfg, sets):
    """Give every URL to exactly one sitemap. Some URLs are produced by several
    groups: /new-york/roof-repair is both a state-service page and, via the
    city of New York, a city-service URL (BD renders the state-service page).
    The group with the higher `priority` wins; on a tie, a group whose BD
    master lists the URL directly beats one that only derives it; after that,
    the first group in config order wins."""
    owner = {}
    for group in cfg["group"]:
        name = group["name"]
        if name not in sets:
            continue
        urls, native = sets[name]
        rank_base = group.get("priority", 0)
        for url in urls:
            rank = (rank_base, url in native)
            if url not in owner or rank > owner[url][1]:
                owner[url] = (name, rank)
    return {name: sorted(u for u, (g, _) in owner.items() if g == name) for name in sets}


# ── Sitemap files ───────────────────────────────────────────────────────

def read_sitemap(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return LOC_RE.findall(f.read())


def write_sitemap(path, entries):
    """entries: list of (url, lastmod) sorted by url."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, lastmod in entries:
        lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        lines.append(f"  <url><loc>{escape(url)}</loc>{lm}</url>")
    lines.append("</urlset>")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


# ── Main ────────────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, STATE_PATH)


def is_due(rec, today, cfg, full):
    if full or rec is None or "checked" not in rec:
        return True
    age = (today - date.fromisoformat(rec["checked"])).days
    limit = cfg["recheck_passing_days"] if rec.get("ok") else cfg["recheck_failing_days"]
    return age >= limit


def run_checks(jobs, cfg):
    """jobs: list of (url, group). Returns {url: result or None}."""
    limiter = RateLimiter(cfg["requests_per_second"])
    deadline = time.monotonic() + cfg["max_runtime_minutes"] * 60
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
        futures = [pool.submit(check_url, u, cfg, g, limiter, deadline) for u, g in jobs]
        for fut in futures:
            url, res = fut.result()
            results[url] = res
            done += 1
            if done % 500 == 0:
                print(f"  checked {done}/{len(jobs)}", flush=True)
    return results


def sample_mode(cfg, n):
    print(f"SAMPLE MODE: {n} random URLs per sitemap, nothing is written.\n")
    random.seed(42)
    for group in cfg["group"]:
        cands = candidates(cfg, group)
        if cands is None:
            continue
        picked = random.sample(cands, min(n, len(cands)))
        res = run_checks([(u, group) for u in picked], cfg)
        reasons = Counter((r["reason"] or "PASS") for r in res.values() if r)
        counts = Counter(r["count"] for r in res.values() if r and r["count"] is not None)
        passed = reasons.get("PASS", 0)
        print(f"[{group['name']}] {len(cands)} candidates, sampled {len(picked)}: "
              f"{passed} pass ({passed / len(picked):.0%}) → about {round(len(cands) * passed / len(picked))} URLs")
        print(f"    reasons: {dict(reasons.most_common())}")
        print(f"    contractor counts: {dict(sorted(counts.items()))}")
        for url, r in list(res.items())[:4]:
            print(f"    e.g. {url} → {r['reason'] or 'PASS'} (count {r['count']})")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="re-check every candidate now")
    ap.add_argument("--accept-drop", action="store_true", help="publish even if a sitemap shrank past the guard")
    ap.add_argument("--sample", type=int, metavar="N", help="test mode: check N random URLs per sitemap, write nothing")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.toml"))
    args = ap.parse_args()

    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)

    if args.sample:
        sample_mode(cfg, args.sample)
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(ALERT_PATH):
        os.remove(ALERT_PATH)
    today = datetime.now(timezone.utc).date()
    state = load_state()
    alerts = []

    # 1. Candidates per group. A group whose source can't be read is skipped
    #    entirely, so its current sitemap stays as it is.
    sets = {}
    for group in cfg["group"]:
        print(f"Loading candidates for {group['name']}…")
        result = candidate_sets(cfg, group)
        if result is None:
            alerts.append(f"**{group['name']}**: a BD master sitemap could not be read, "
                          f"so this sitemap was left unchanged.")
            continue
        sets[group["name"]] = result
    group_cands = assign_groups(cfg, sets)
    for name, cands in group_cands.items():
        print(f"  {name}: {len(cands)} candidates")

    # 2. Decide what to check: new URLs first, then listed pages, then failing ones.
    jobs = []
    for group in cfg["group"]:
        for url in group_cands.get(group["name"], []):
            rec = state.get(url)
            if is_due(rec, today, cfg, args.full):
                priority = 0 if rec is None else (1 if rec.get("ok") else 2)
                jobs.append((priority, rec.get("checked", "") if rec else "", url, group))
    jobs.sort(key=lambda j: (j[0], j[1]))
    if not args.full:
        jobs = jobs[:cfg["max_checks_per_run"]]
    jobs = [(u, g) for _, _, u, g in jobs]
    print(f"Checking {len(jobs)} pages…")

    # 3. Crawl.
    results = run_checks(jobs, cfg)
    attempted = [r for r in results.values() if r is not None]
    transient = sum(1 for r in attempted if r["reason"] == TRANSIENT)
    if attempted and transient / len(attempted) > cfg["max_transient_ratio"]:
        msg = (f"{transient} of {len(attempted)} checks were blocked or failed (403/429/5xx/timeouts). "
               f"The site may be blocking the builder. Nothing was published and verdicts were not updated.")
        print("ABORT: " + msg)
        with open(ALERT_PATH, "w", encoding="utf-8") as f:
            f.write(f"# Sitemap build aborted {today}\n\n{msg}\n")
        return 0

    # 4. Record verdicts. Transient results keep the previous verdict.
    group_of = {u: g["name"] for g in cfg["group"] for u in group_cands.get(g["name"], [])}
    for url, r in results.items():
        if r is None or r["reason"] == TRANSIENT:
            continue
        rec = state.get(url, {"first_seen": today.isoformat()})
        changed = rec.get("ok") != r["ok"] or rec.get("count") != r["count"]
        rec.update({"checked": today.isoformat(), "ok": r["ok"], "reason": r["reason"],
                    "count": r["count"], "status": r["status"], "noindex": r["noindex"],
                    "modified": r["modified"], "group": group_of[url]})
        if changed or "changed" not in rec:
            rec["changed"] = today.isoformat()
        state[url] = rec

    # Forget URLs that dropped out of a master sitemap (only for groups we could read).
    for url in list(state):
        if url not in group_of and state[url].get("group") in group_cands:
            del state[url]
    save_state(state)

    # 5. Write sitemaps, behind the shrink guard.
    report = [f"# Sitemap build {today}", "",
              f"Checked {len(attempted)} pages this run ({transient} transient, kept previous verdict).", ""]
    for group in cfg["group"]:
        name = group["name"]
        if name not in group_cands:
            continue
        out_path = os.path.join(OUT_DIR, group["output"])
        cands = group_cands[name]
        lastmod_key = "modified" if group.get("lastmod") == "dateModified" else "changed"
        listed = sorted((u, state[u].get(lastmod_key) or state[u].get("changed"))
                        for u in cands if state.get(u, {}).get("ok"))
        unchecked = sum(1 for u in cands if u not in state)
        previous = read_sitemap(out_path)
        new_urls = {u for u, _ in listed}
        prev_urls = set(previous or [])

        blocked = None
        if len(listed) < group["min_urls"]:
            blocked = f"only {len(listed)} URLs pass (minimum {group['min_urls']})"
        elif previous and len(listed) < len(previous) * (1 - cfg["guard"]["max_drop"]) and not args.accept_drop:
            blocked = (f"would shrink from {len(previous)} to {len(listed)} URLs "
                       f"(more than {cfg['guard']['max_drop']:.0%})")
        if blocked and unchecked and not previous:
            blocked += f"; {unchecked} candidates not checked yet — run with --full first"

        if blocked:
            alerts.append(f"**{name}**: {blocked}. The previous sitemap was kept. "
                          f"If the drop is intended, re-run the workflow with accept_drop.")
        else:
            write_sitemap(out_path, listed)

        reasons = Counter(state[u].get("reason") or "pass" for u in cands if u in state)
        added, removed = sorted(new_urls - prev_urls), sorted(prev_urls - new_urls)
        report += [f"## {name}" + (" — NOT PUBLISHED" if blocked else ""), "",
                   f"- Candidates: {len(cands)} (not yet checked: {unchecked})",
                   f"- Listed: {len(listed)} (previous: {len(previous) if previous is not None else '–'})",
                   f"- Added: {len(added)}, removed: {len(removed)}",
                   f"- Verdicts: " + ", ".join(f"{k} {v}" for k, v in reasons.most_common()), ""]
        for label, items in (("Added", added), ("Removed", removed)):
            if items:
                report.append(f"<details><summary>{label} ({len(items)})</summary>\n")
                for u in items[:200]:
                    why = state.get(u, {}).get("reason") or ""
                    report.append(f"- {u}" + (f" ({why})" if why else ""))
                report.append("\n</details>\n")

    # Thin pages that Google can still index: the C2 to-do list.
    thin_indexable = sorted(u for u, r in state.items()
                            if r.get("reason") == "thin" and not r.get("noindex"))
    with open(os.path.join(OUT_DIR, "thin-but-indexable.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(f"{state[u].get('count')}\t{u}" for u in thin_indexable) + "\n")
    report += ["## Thin pages Google can still index", "",
               f"{len(thin_indexable)} pages fail the contractor minimum but are not noindexed. "
               f"Full list: `output/thin-but-indexable.txt`. Excluding them from sitemaps does not "
               f"remove them from Google; noindex them (C2).", ""]

    with open(os.path.join(OUT_DIR, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    if alerts:
        with open(ALERT_PATH, "w", encoding="utf-8") as f:
            f.write(f"# Sitemap build {today}: needs attention\n\n" + "\n".join(f"- {a}" for a in alerts) + "\n")
        print("ALERTS:\n" + "\n".join(alerts))
    print("Done. See output/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
