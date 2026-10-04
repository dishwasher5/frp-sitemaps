# frp-sitemaps

Thin-page-free sitemaps for findroofingpros.com, rebuilt daily by GitHub Actions.

Every candidate page (from Brilliant Directories' own master sitemaps) is fetched and listed
only if it:

- returns **200**
- is **indexable** (no `noindex` in robots meta or `X-Robots-Tag`)
- is **self-canonical**
- shows at least **`min_contractors`** listings (microdata `numberOfItems`)

Rules, thresholds and exclusions live in [`config.toml`](config.toml).

## Safety rules

- **Shrink guard:** a sitemap that would lose more than 20% of its URLs in one run is not
  published. The old one stays, and the run fails so GitHub emails you. Re-run with
  `accept_drop` if the drop is intended.
- **Minimum size:** a sitemap below `min_urls` is never published.
- **Block detection:** if more than 20% of checks get 403/429/5xx/timeouts, nothing is
  published and no verdicts change.
- **Unreadable source:** if a BD master sitemap can't be read, that sitemap is left unchanged.
- **The BD proxy widget** serves its last good copy (or a 503) if GitHub is unreachable or
  returns anything that isn't a real sitemap.

## Files

| Path | What it is |
|---|---|
| `build_sitemaps.py` | The builder (Python 3.11+, standard library only) |
| `config.toml` | Rules, thresholds, sitemap definitions |
| `.github/workflows/build.yml` | Daily schedule + manual run |
| `widget/sitemap-proxy-widget.php` | Paste into each BD sitemap widget |
| `output/sitemap-*.xml` | The published sitemaps |
| `output/report.md` | What was added/removed and why (also shown on each Actions run) |
| `output/thin-but-indexable.txt` | Thin pages Google can still index: the noindex to-do list |
| `state/checks.json` | Last verdict per URL |

## Setup

1. Create a **public** repo named `frp-sitemaps` on GitHub (public, so the BD widget can read
   the files without a token; sitemaps are public anyway).
2. Push this folder to it.
3. In the repo: **Actions** → enable workflows → **Build sitemaps** → **Run workflow** with
   **full** ticked. The first run checks about 12,400 pages and takes about 1h45m.
4. Check `output/report.md` and the three sitemap files.
5. In BD, replace the code of the three existing sitemap widgets with
   `widget/sitemap-proxy-widget.php`, setting `$FILE` and `$GITHUB_USER` in each.
6. Turn off the old cron-job.org updater jobs.

## Test locally

```
python3 build_sitemaps.py --sample 60   # checks 60 random pages per sitemap, writes nothing
```

## Changing rules

Edit `config.toml`, commit, then run the workflow with **full** ticked so every page is
judged by the new rules. If the change removes more than 20% of a sitemap, also tick
**accept_drop**.

Note: GitHub pauses scheduled workflows in repos with no activity for 60 days. The daily
commits from the builder count as activity, so this only matters if every run fails.
