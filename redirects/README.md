# C1: flat city URLs → /{state}/{city}

BD now outputs self-canonical `/{state}/{city}` and `/{state}/{city}/{service}` pages
(ticket #599442, "Localize Canonical Tags" off). This folder retires the flat
`/{city}` and `/{city}/{service}` duplicates.

## What happens to each flat URL

| Flat city name | Rule | Count |
|---|---|---|
| Listed in one state | 301 to that state | 3,477 |
| Listed in 2+ states, leader has ≥ 2× the runner-up's listings | 301 to the leader | 344 |
| Listed in 2+ states, no clear leader | Flat URL stays, shows a "Which Springfield?" chooser | 206 |
| Same slug as a state (`/new-york`, `/washington`…) | Untouched (BD state page) | 8 |

Flat `/{city}/{service}` URLs follow their city: 301 to the same service in that state.
If that page doesn't exist (404 or not self-canonical, per `verify_targets.py`), they
go to the `/{state}/{city}` page instead. Choosers keep their flat service URLs.

Live counts are in `summary.json`.

## Order (don't skip ahead)

1. **Sitemaps first** (done in commit `0fa73d5`): the builder now lists state-form URLs.
   In GitHub → Actions → Build sitemaps → Run workflow, tick **full**. The new URLs
   need checking once; expect about 2 hours. Check that `output/report.md` shows
   city ≈ 2,000 and city-service ≈ 1,600 listed.
2. **Chooser snippet**: paste `widget/c1-city-chooser.php` into the BD search-results
   template, directly below the C2 snippet (the `$frp_count` / `X-Robots-Tag` lines,
   above `// ItemList schema`). It's already inside PHP, so it has no `<?php` tags.
   Check `/springfield` and `/springfield/roof-repair`: a "Which Springfield?" box with
   7 states. Check `/houston` and `/texas/dallas`: no box, page renders normally, no
   "Widget error". If anything breaks, delete the snippet and send me the error.
3. **Test import**: Developer Hub → 301 Redirects → Import CSV File → `301-test.csv`
   (3 rows). Check:
   `curl -sI https://www.findroofingpros.com/houston` → `301` to `/texas/houston` in one hop.
   If the redirect doesn't fire, the paths probably need a different format (full URL,
   or no leading slash). Export an existing redirect to see BD's format and tell me.
4. **Full import**: `301-part01.csv` … `301-part06.csv` (2,000 rows each), in order.
   `301-test.csv` rows are also in the parts; if BD rejects duplicates, delete the
   3 test redirects first.
5. **Afterwards**: point internal links and llms.txt at `/{state}/{city}` (L17).

## Regenerating

```
python3 redirects/make_redirects.py      # rebuild the map from BD's live sitemaps
python3 redirects/verify_targets.py      # check destinations (resumable, ~3 req/s)
python3 redirects/make_redirects.py      # again, so dead targets fall back
```

`301-*.csv`, `target-status.json` and `unmapped.txt` are local outputs and aren't committed.
