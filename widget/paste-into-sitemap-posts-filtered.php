<?php
/* ============================================================
   FRP - SITEMAP PROXY WIDGET (v2, BD-sandbox-safe)
   ============================================================
   Serves a sitemap built by the frp-sitemaps GitHub repo at the
   site's existing sitemap URL. Use one copy per sitemap page,
   changing only $SITEMAP_FILE:

     sitemap-city-filtered          -> 'sitemap-city.xml'
     sitemap-city-service-filtered  -> 'sitemap-city-service.xml'
     sitemap-state-service-filtered -> 'sitemap-state-service.xml'
     sitemap-state-filtered         -> 'sitemap-state.xml' (MIN_URLS 40)
     sitemap-posts-filtered         -> 'sitemap-posts.xml' (MIN_URLS 30)

   If GitHub can't be reached, or returns something that isn't a
   real sitemap, the last good copy is served (or a 503, which makes
   Google keep its previous version). It never serves an empty list.

   Uses only PHP functions already proven to run in this site's
   BD widgets (the old updater and output widgets).
   ============================================================ */

$SITEMAP_FILE = 'sitemap-posts.xml';    // <- change per page (see list above)
$GITHUB_USER = 'dishwasher5';
$REPO        = 'frp-sitemaps';
$MIN_URLS    = 30;                     // a fetched file with fewer URLs than this is treated as broken

$SOURCE    = 'https://raw.githubusercontent.com/' . $GITHUB_USER . '/' . $REPO . '/main/output/' . $SITEMAP_FILE;
$LAST_GOOD = sys_get_temp_dir() . '/frp-proxy-' . $SITEMAP_FILE; // safety net only

function frp_count_locs($xml, $stopAt) {
    $n = 0;
    $offset = 0;
    while ($n < $stopAt) {
        $pos = strpos($xml, '<loc>', $offset);
        if ($pos === false) break;
        $n++;
        $offset = $pos + 5;
    }
    return $n;
}

$ch = curl_init();
curl_setopt_array($ch, [
    CURLOPT_URL => $SOURCE,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 15,
]);
$xml = curl_exec($ch);
$err = curl_error($ch);
curl_close($ch);

$valid = !$err && $xml
      && strpos($xml, '<urlset') !== false
      && strpos($xml, '</urlset>') !== false
      && frp_count_locs($xml, $MIN_URLS) >= $MIN_URLS;

if ($valid) {
    file_put_contents($LAST_GOOD, $xml);
    header('Content-Type: application/xml; charset=UTF-8');
    echo $xml;
} elseif (file_exists($LAST_GOOD)) {
    header('Content-Type: application/xml; charset=UTF-8');
    echo file_get_contents($LAST_GOOD);
} else {
    header('HTTP/1.1 503 Service Unavailable');
    header('Retry-After: 3600');
    header('Content-Type: text/plain');
    echo 'Sitemap temporarily unavailable.';
}
exit;
?>