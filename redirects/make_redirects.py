#!/usr/bin/env python3
"""C1: 301 map from flat /{city} and /{city}/{service} URLs to /{state}/{city}...,
plus the "Which Springfield?" chooser list for city names that are shared
between states with no clear leader.

Inputs are BD's own master sitemaps (read live). Contractor counts per
state/city come from the profile sitemap (/{state}/{city}/{member}).

Rule for each flat city slug (decided on profile counts per state):
  - listed in exactly one state          -> 301 to that state
  - listed in 2+ states, leader has at
    least LEADER_RATIO x the runner-up   -> 301 to the leader
  - otherwise                            -> chooser (flat URL stays)
  - no profiles anywhere: one state among BD's state/city/service pages
    -> 301 there, several -> chooser
Flat /{city}/{service} URLs follow their city: 301 to the same service in the
chosen state if BD has that page, otherwise to the state/city page; chooser
cities keep their flat service URLs as choosers too.
Slugs that are also state slugs (/new-york, /washington…) are BD state pages
and are never touched.

Usage:  python3 redirects/make_redirects.py      (writes into redirects/ and widget/)
"""
import csv
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict

SITE = "https://www.findroofingpros.com"
SM = SITE + "/filedata/cache/xml-sitemaps/"
UA = "Mozilla/5.0 (compatible; FRPSitemapBuilder/1.0; +https://www.findroofingpros.com)"
LEADER_RATIO = 2.0
CSV_CHUNK = 2000            # rows per CSV file, so one bad import is easy to redo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district-of-columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new-hampshire": "NH", "new-jersey": "NJ", "new-mexico": "NM", "new-york": "NY",
    "north-carolina": "NC", "north-dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode-island": "RI", "south-carolina": "SC", "south-dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west-virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def paths(name):
    req = urllib.request.Request(SM + name, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", "replace")
    out = [re.sub(r"^https?://[^/]+", "", u).strip("/") for u in re.findall(r"<loc>\s*(.*?)\s*</loc>", body)]
    if not out:
        sys.exit(f"empty or unreadable sitemap: {name}")
    return out


def main():
    states = {p for p in paths("profile_search_results-state_name-1.xml") if "/" not in p}
    flat_cities = list(dict.fromkeys(p for p in paths("profile_search_results-city_name-1.xml") if "/" not in p))  # BD repeats shared slugs once per state
    flat_cs = list(dict.fromkeys(p for p in paths("profile_search_results-city_name-service_name-1.xml") if p.count("/") == 1))
    state_cs = {p for p in paths("profile_search_results-state_name-city_name-service_name-1.xml")
                if p.count("/") == 2}

    profiles = Counter()
    for p in paths("profile-filename-1.xml"):
        seg = p.split("/")
        if len(seg) == 3 and seg[0] in states:
            profiles[(seg[0], seg[1])] += 1

    states_of = defaultdict(set)                     # city slug -> states with any BD page
    for p in state_cs:
        s, c, _ = p.split("/")
        states_of[c].add(s)
    for (s, c) in profiles:
        states_of[c].add(s)

    decision = {}                                    # city -> ("301", state) | ("chooser", [(n, state)])
    for c in flat_cities:
        if c in states:
            continue
        ranked = sorted(((profiles[(s, c)], s) for s in states_of.get(c, ())), reverse=True)
        listed = [x for x in ranked if x[0] > 0]
        if len(listed) == 1:
            decision[c] = ("301", listed[0][1])
        elif len(listed) >= 2:
            if listed[0][0] >= LEADER_RATIO * listed[1][0]:
                decision[c] = ("301", listed[0][1])
            else:
                decision[c] = ("chooser", listed)
        elif len(ranked) == 1:
            decision[c] = ("301", ranked[0][1])
        elif len(ranked) > 1:
            decision[c] = ("chooser", ranked)
        # no state at all: leave alone (reported in unmapped.txt)

    rows, choosers, chooser_cs, unmapped = [], [], [], []
    for c in sorted(decision):
        kind, val = decision[c]
        if kind == "301":
            rows.append(("/" + c, f"/{val}/{c}", "city"))
        else:
            choosers.append(c)
    for p in sorted(flat_cs):
        c, svc = p.split("/")
        if c in states:
            continue
        if c not in decision:
            unmapped.append(p)
            continue
        kind, val = decision[c]
        if kind == "chooser":
            chooser_cs.append(p)
        elif f"{val}/{c}/{svc}" in state_cs:
            rows.append(("/" + p, f"/{val}/{c}/{svc}", "city-service"))
        else:
            rows.append(("/" + p, f"/{val}/{c}", "city-service-fallback"))
    unmapped += [c for c in flat_cities if c not in states and c not in decision]

    # Use verify_targets.py results when present: BD's sitemaps list some
    # state/city/service pages that 404. Those fall back to the state/city page;
    # a city row whose target fails is dropped (flat URL stays) and reported.
    status_path = os.path.join(HERE, "target-status.json")
    target_status = json.load(open(status_path)) if os.path.exists(status_path) else {}
    good = lambda d: d not in target_status or (target_status[d].get("status") == 200
                                                 and target_status[d].get("self_canonical"))
    dead_targets = []
    checked = []
    for src, dst, kind in rows:
        if good(dst):
            checked.append((src, dst, kind))
            continue
        city_dst = "/" + "/".join(dst.strip("/").split("/")[:2])
        if kind == "city-service" and good(city_dst):
            checked.append((src, city_dst, "city-service-fallback"))
        else:
            dead_targets.append(f"{src} -> {dst} ({target_status[dst].get('status')})")
    rows = checked

    # Never redirect onto another redirect source (would create a chain).
    sources = {r[0] for r in rows}
    chained = [r for r in rows if r[1] in sources]
    assert not chained, f"redirect chains: {chained[:5]}"

    # ── outputs ─────────────────────────────────────────────────────────
    for old in os.listdir(HERE):
        if old.startswith("301-") and old.endswith(".csv"):
            os.remove(os.path.join(HERE, old))
    for i in range(0, len(rows), CSV_CHUNK):
        with open(os.path.join(HERE, f"301-part{i // CSV_CHUNK + 1:02d}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["source_url", "destination_url", "type", "db_id"])
            for src, dst, _ in rows[i:i + CSV_CHUNK]:
                w.writerow([src, dst, "import", ""])
    with open(os.path.join(HERE, "301-test.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_url", "destination_url", "type", "db_id"])
        for want in ("/houston", "/houston/roof-repair", "/portland"):
            hit = next((r for r in rows if r[0] == want), None)
            if hit:
                w.writerow([hit[0], hit[1], "import", ""])

    with open(os.path.join(HERE, "chooser-cities.txt"), "w") as f:
        f.write("\n".join(choosers) + "\n")
    with open(os.path.join(HERE, "chooser-city-services.txt"), "w") as f:
        f.write("\n".join(chooser_cs) + "\n")

    # Data the chooser widget needs: city -> [[state, abbr, count, "svc svc …"]]
    svc_by = defaultdict(list)
    chooser_set = set(choosers)
    for p in state_cs:
        s, c, svc = p.split("/")
        if c in chooser_set:
            svc_by[(c, s)].append(svc)
    chooser_data = {c: [[s, STATE_ABBR.get(s, s.upper()), n, " ".join(sorted(svc_by[(c, s)]))]
                        for n, s in decision[c][1]] for c in choosers}
    with open(os.path.join(HERE, "chooser-data.json"), "w") as f:
        json.dump(chooser_data, f, separators=(",", ":"), sort_keys=True)
    write_widget(chooser_data)

    n_listed = lambda c: sum(1 for s in states_of[c] if profiles[(s, c)] > 0)
    kinds = Counter(k for _, _, k in rows)
    summary = {
        "flat_cities": len(flat_cities),
        "skipped_state_slugs": sum(1 for c in flat_cities if c in states),
        "city_301": kinds["city"],
        "city_301_single_state": sum(1 for c, (k, _) in decision.items() if k == "301" and n_listed(c) <= 1),
        "city_301_clear_leader": sum(1 for c, (k, _) in decision.items() if k == "301" and n_listed(c) >= 2),
        "chooser_cities": len(choosers),
        "flat_city_services": len(flat_cs),
        "city_service_301": kinds["city-service"],
        "city_service_301_to_city_fallback": kinds["city-service-fallback"],
        "chooser_city_services": len(chooser_cs),
        "unmapped": len(unmapped),
        "targets_verified": len(target_status),
        "dropped_dead_targets": len(dead_targets),
        "total_301_rows": len(rows),
        "csv_files": (len(rows) + CSV_CHUNK - 1) // CSV_CHUNK,
    }
    with open(os.path.join(HERE, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(HERE, "unmapped.txt"), "w") as f:
        f.write("\n".join(unmapped + dead_targets) + "\n")
    print(json.dumps(summary, indent=2))


def write_widget(data):
    """PHP for BD's search-results template: shows the chooser on flat chooser URLs."""
    def php_str(s):
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
    entries = []
    for c in sorted(data):
        states = ",".join(
            "array(" + ",".join([php_str(s), php_str(a), str(n), php_str(svcs)]) + ")"
            for s, a, n, svcs in data[c])
        entries.append(f"                    {php_str(c)} => array({states})")
    php = PHP_TEMPLATE.replace("/*DATA*/", ",\n".join(entries))
    with open(os.path.join(ROOT, "widget", "c1-city-chooser.php"), "w") as f:
        f.write(php)


PHP_TEMPLATE = r"""            // C1: "Which Springfield?" chooser on flat city URLs shared by several
            // states with no clear leader. Generated by redirects/make_redirects.py:
            // re-run it and re-paste rather than editing by hand.
            // Paste into the BD search-results template on its own lines, directly
            // below the C2 snippet (already inside PHP, so no <?php tags).
            // Prints nothing on any other page.
            $frp_cc_path = trim((string) parse_url(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '', PHP_URL_PATH), '/');
            $frp_cc_seg = $frp_cc_path === '' ? array() : explode('/', strtolower($frp_cc_path));
            if (count($frp_cc_seg) >= 1 && count($frp_cc_seg) <= 2) {
                $frp_cc_data = array(
/*DATA*/
                );
                $frp_cc_city = $frp_cc_seg[0];
                $frp_cc_svc = isset($frp_cc_seg[1]) ? $frp_cc_seg[1] : '';
                if (isset($frp_cc_data[$frp_cc_city])) {
                    $frp_cc_name = ucwords(str_replace('-', ' ', $frp_cc_city));
                    $frp_cc_svc_name = $frp_cc_svc !== '' ? ucwords(str_replace('-', ' ', $frp_cc_svc)) : '';
                    echo '<nav class="frp-city-chooser" aria-label="Choose a state" style="border:1px solid #d8dee4;border-radius:8px;padding:14px 16px;margin:0 0 18px;background:#f6f8fa;">';
                    echo '<p style="margin:0 0 8px;font-weight:700;font-size:16px;">Which ' . htmlspecialchars($frp_cc_name) . '? '
                        . '<span style="font-weight:400;">This page mixes contractors from every ' . htmlspecialchars($frp_cc_name)
                        . '. Pick your state for local results' . ($frp_cc_svc_name !== '' ? ' for ' . htmlspecialchars($frp_cc_svc_name) : '') . ':</span></p>';
                    echo '<ul style="list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:8px;">';
                    foreach ($frp_cc_data[$frp_cc_city] as $frp_cc_st) {
                        $frp_cc_has_svc = $frp_cc_svc !== '' && in_array($frp_cc_svc, explode(' ', $frp_cc_st[3]), true);
                        $frp_cc_href = '/' . $frp_cc_st[0] . '/' . $frp_cc_city . ($frp_cc_has_svc ? '/' . $frp_cc_svc : '');
                        echo '<li><a href="' . htmlspecialchars($frp_cc_href) . '" style="display:inline-block;padding:6px 12px;border:1px solid #c9d3dc;border-radius:6px;background:#fff;text-decoration:none;">'
                            . htmlspecialchars($frp_cc_name) . ', ' . htmlspecialchars($frp_cc_st[1])
                            . ' <span style="color:#57606a;">(' . (int) $frp_cc_st[2] . ')</span></a></li>';
                    }
                    echo '</ul></nav>';
                }
            }
"""

if __name__ == "__main__":
    main()
