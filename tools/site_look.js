// Look at live-site pages, read-only BY CONSTRUCTION: this tool can only sign in, open URLs,
// read text and take screenshots. It has no click, type or submit step, so it cannot change data.
//
// usage: node site_look.js <accounts.json> <accountKey> <siteUrl> <outDir> <path> [<path> ...]
//   e.g. node site_look.js roster.json test-user https://app.example.com out/ /Orders /Home
// Prints one JSON line: [{path, url, title, ok, screenshot, text(first 4000 chars), error}]
const path = require('path'), fs = require('fs');
// Always close our headless browser, even on error or timeout.
let __browser = null;
const __close = () => { try { __browser && __browser.close(); } catch (e) {} };
process.on('SIGTERM', () => { __close(); process.exit(1); });
process.on('SIGINT', () => { __close(); process.exit(1); });
const { loadPlaywright, launchOptions, account, signIn } = require('./browser');
const [, , accountsFile, accountKey, site, outDir, ...paths] = process.argv;

(async () => {
  const { chromium } = loadPlaywright();
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch(launchOptions()); __browser = browser;
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const results = [];
  const siteHost = new URL(site).host;
  const landed = await signIn(page, site, account(accountsFile, accountKey));
  if (new URL(landed).host !== siteHost) {
    // Still on the sign-in server: the account has no access to this site. Report it; screenshots would only show a login page.
    console.log(JSON.stringify([{ error: `sign-in did not reach ${siteHost} (landed on ${landed}); this account has no access to this site` }]));
    await browser.close(); return;
  }
  for (const [i, p] of paths.entries()) {
    const url = new URL(p, site).toString(); const r = { path: p, url };
    try {
      // Pages with live connections never go network-idle, so wait for the DOM, then settle briefly.
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
      r.status = resp ? resp.status() : null; r.ok = !!resp && resp.ok();
      r.final = page.url().replace(/\?.*/, '');
      if (new URL(r.final).host !== siteHost) { r.ok = false; r.error = `redirected off the site to ${r.final} (no access or session lost)`; }
      r.title = await page.title();
      // Links on the page, so a list can lead to a real record (e.g. a case) on the next run - still read-only.
      r.links = await page.evaluate(() => [...document.querySelectorAll('a[href]')]
        .map(a => ({ text: (a.innerText || '').trim().slice(0, 60), href: a.getAttribute('href') }))
        .filter(l => l.href && !l.href.startsWith('#') && !/^javascript:/i.test(l.href)
                && !/logout|signout|delete|remove|approve|reject|submit/i.test(l.href))
        .filter((l, i, all) => all.findIndex(x => x.href === l.href) === i)          // de-duplicate
        .sort((a, b) => (/[?&][a-z]*id=/i.test(b.href) ? 1 : 0) - (/[?&][a-z]*id=/i.test(a.href) ? 1 : 0))  // record links first
        .slice(0, 300));
      r.text = (await page.evaluate(() => document.body.innerText)).replace(/\n{3,}/g, '\n\n').slice(0, 4000);
      r.screenshot = path.join(outDir, `${String(i + 1).padStart(2, '0')}_${p.replace(/[^a-z0-9]+/gi, '_').slice(0, 60)}.png`);
      await page.screenshot({ path: r.screenshot, fullPage: true });
    } catch (e) { r.error = e.message.split('\n')[0]; }
    results.push(r);
  }
  await browser.close();
  console.log(JSON.stringify(results));
})().catch(async e => { console.error(e.message); try { __browser && await __browser.close(); } catch (x) {} process.exit(1); });
