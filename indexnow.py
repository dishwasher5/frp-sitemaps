#!/usr/bin/env python3
"""Submit changed sitemap URLs to IndexNow (Bing, Yandex, Seznam, Naver…).

Runs after build_sitemaps.py. Compares the published sitemaps in output/
with what was last submitted (state/indexnow.json) and sends:
  - URLs that are new to the sitemaps,
  - URLs whose <lastmod> changed,
  - URLs that dropped out of the sitemaps (so engines recrawl and see the
    404/noindex/redirect), once.
Nothing is sent, and the state is left alone, unless the key file is live.
A failed submission never fails the build: it is reported and retried next run.

Standard library only (Python 3.11+).

Usage:
  python indexnow.py             # submit what changed
  python indexnow.py --dry-run   # show what would be sent, send nothing
"""

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from glob import glob
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "output")
STATE_PATH = os.path.join(ROOT, "state", "indexnow.json")
BATCH_SIZE = 10000  # IndexNow maximum per request

ENTRY_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>(?:\s*<lastmod>\s*(.*?)\s*</lastmod>)?", re.S)


def read_entries():
    """{url: lastmod} from every published sitemap in output/."""
    entries = {}
    for path in sorted(glob(os.path.join(OUT_DIR, "sitemap-*.xml"))):
        with open(path, encoding="utf-8") as f:
            for loc, lastmod in ENTRY_RE.findall(f.read()):
                entries[loc.replace("&amp;", "&")] = lastmod or ""
    return entries


def summary(line):
    """Print, and add to the Actions run summary when there is one."""
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n\n")


def key_is_live(cfg, user_agent):
    req = urllib.request.Request(cfg["key_location"], headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200 and resp.read().decode("utf-8", "replace").strip() == cfg["key"]
    except Exception:
        return False


def submit(cfg, host, urls):
    payload = json.dumps({"host": host, "key": cfg["key"], "keyLocation": cfg["key_location"],
                          "urlList": urls}).encode("utf-8")
    req = urllib.request.Request(cfg["endpoint"], data=payload, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return None, str(e)[:200]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "config.toml"), "rb") as f:
        full_cfg = tomllib.load(f)
    cfg = full_cfg.get("indexnow", {})
    if not cfg.get("enabled"):
        print("IndexNow disabled in config.toml.")
        return 0
    host = urlsplit(full_cfg["site"]).netloc

    current = read_entries()
    if not current:
        summary("IndexNow: no sitemap URLs found in output/, nothing sent.")
        return 0
    submitted = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            submitted = json.load(f)

    changed = sorted(u for u, lm in current.items() if submitted.get(u) != lm)
    dropped = sorted(u for u in submitted if u not in current)
    queue = changed + dropped
    print(f"IndexNow: {len(current)} sitemap URLs; {len(changed)} new or changed, {len(dropped)} dropped.")
    if args.dry_run:
        for u in queue[:30]:
            print("  " + u)
        if len(queue) > 30:
            print(f"  … and {len(queue) - 30} more")
        return 0
    if not queue:
        return 0

    if not key_is_live(cfg, full_cfg["user_agent"]):
        summary(f"**IndexNow:** {len(queue)} URLs waiting, but the key file isn't live at "
                f"{cfg['key_location']} (it must return 200 and contain only the key). Nothing sent; "
                f"they'll go once it is.")
        return 0

    sent = 0
    for i in range(0, len(queue), BATCH_SIZE):
        batch = queue[i:i + BATCH_SIZE]
        status, detail = submit(cfg, host, batch)
        if status not in (200, 202):
            # 403 key not valid, 422 URL not on this host, 429 too many requests
            summary(f"**IndexNow:** batch of {len(batch)} failed (HTTP {status}) {detail}. "
                    f"Sent {sent} before it; the rest retry next run.")
            break
        for u in batch:
            if u in current:
                submitted[u] = current[u]
            else:
                submitted.pop(u, None)
        sent += len(batch)

    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(submitted, f, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, STATE_PATH)
    if sent:
        summary(f"IndexNow: submitted {sent} URLs ({len(changed)} new or changed, {len(dropped)} dropped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
