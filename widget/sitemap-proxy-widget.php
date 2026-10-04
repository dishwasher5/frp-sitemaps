<?php
/* ============================================================
   FRP — SITEMAP PROXY WIDGET
   ============================================================
   Serves a sitemap built by the frp-sitemaps GitHub repo at the
   site's existing sitemap URL. Use one copy per sitemap page,
   changing only $FILE:

     sitemap-city-filtered          → 'sitemap-city.xml'
     sitemap-city-service-filtered  → 'sitemap-city-service.xml'
     sitemap-state-service-filtered → 'sitemap-state-service.xml'

   DEPLOY: Toolbox → Widget Manager → open the widget behind each
   existing sitemap page and replace its code with this. Then turn
   off the old cron-job.org updater jobs.

   If GitHub can't be reached, or returns something that isn't a
   real sitemap, the last good copy is served (or a 503, which makes
   Google keep its previous version). It never serves an empty list.
   BD widget limits respected: no preg_* functions, no $_GET.
   ============================================================ */

$FILE        = 'sitemap-city.xml';     // ← change per page (see list above)
$GITHUB_USER = 'dishwasher5';     // ← your GitHub username
$REPO        = 'frp-sitemaps';
$MIN_URLS    = 50;                     // a fetched file with fewer URLs than this is treated as broken

$SOURCE    = 'https://raw.githubusercontent.com/' . $GITHUB_USER . '/' . $REPO . '/main/output/' . $FILE;
$LAST_GOOD = sys_get_temp_dir() . '/frp-proxy-' . $FILE; // safety net only

$ch = curl_init();
curl_setopt_array($ch, array(
    CURLOPT_URL => $SOURCE,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 15,
    CURLOPT_FOLLOWLOCATION => true,
));
$xml  = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

$valid = $code == 200 && $xml
      && strpos($xml, '<urlset') !== false
      && strpos($xml, '</urlset>') !== false
      && substr_count($xml, '<loc>') >= $MIN_URLS;

if ($valid) {
    @file_put_contents($LAST_GOOD . '.tmp', $xml);
    @rename($LAST_GOOD . '.tmp', $LAST_GOOD);
    header('Content-Type: application/xml; charset=UTF-8');
    header('Cache-Control: public, max-age=3600');
    echo $xml;
} elseif (is_file($LAST_GOOD)) {
    header('Content-Type: application/xml; charset=UTF-8');
    header('Cache-Control: no-store');
    readfile($LAST_GOOD);
} else {
    http_response_code(503);
    header('Retry-After: 3600');
    header('Content-Type: text/plain');
    echo 'Sitemap temporarily unavailable.';
}
exit;
