#!/usr/bin/env python3
"""Fetch every 301 destination in redirects/301-part*.csv once and record its
status, canonical and contractor count in redirects/target-status.json.
make_redirects.py reads that file and sends any destination that isn't a
200, self-canonical page to the /{state}/{city} page instead.

Usage:  python3 redirects/verify_targets.py   (resumable; ~3 requests/second)
"""
import csv
import glob
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SITE = "https://www.findroofingpros.com"
UA = "Mozilla/5.0 (compatible; FRPSitemapBuilder/1.0; +https://www.findroofingpros.com)"
PER_SECOND = 3.0
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "target-status.json")

_lock = threading.Lock()
_next = [time.monotonic()]


def wait():
    with _lock:
        now = time.monotonic()
        t = max(now, _next[0])
        _next[0] = t + 1.0 / PER_SECOND
    time.sleep(max(0.0, t - now))


def check(path):
    for attempt in range(3):
        wait()
        try:
            req = urllib.request.Request(SITE + path, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", "replace")
                code, final = r.status, r.url
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) or e.code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            return path, {"status": e.code}
        except Exception:
            time.sleep(5 * (attempt + 1))
            continue
        can = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', body)
        n = re.search(r'itemprop="numberOfItems" content="(\d+)"', body)
        canonical = can.group(1).rstrip("/") if can else ""
        return path, {"status": code,
                      "self_canonical": canonical == (SITE + path).rstrip("/"),
                      "redirected_to": final if final.rstrip("/") != (SITE + path).rstrip("/") else "",
                      "count": int(n.group(1)) if n else None}
    return path, {"status": "transient"}


def main():
    dests = set()
    for f in glob.glob(os.path.join(HERE, "301-part*.csv")):
        for row in csv.DictReader(open(f, newline="")):
            dests.add(row["destination_url"])
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    todo = sorted(d for d in dests if d not in done or done[d].get("status") == "transient")
    print(f"{len(dests)} destinations, {len(todo)} to check", flush=True)
    with ThreadPoolExecutor(4) as ex:
        for i, (path, info) in enumerate(ex.map(check, todo), 1):
            done[path] = info
            if i % 250 == 0 or i == len(todo):
                with open(OUT + ".tmp", "w") as f:
                    json.dump(done, f, sort_keys=True)
                os.replace(OUT + ".tmp", OUT)
                print(f"  {i}/{len(todo)}", flush=True)
    bad = {p: v for p, v in done.items() if p in dests and not (v.get("status") == 200 and v.get("self_canonical"))}
    print(f"not 200 + self-canonical: {len(bad)}")


if __name__ == "__main__":
    main()
