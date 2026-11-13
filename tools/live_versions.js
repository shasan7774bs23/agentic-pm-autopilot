// Reads the version shown in each live site's footer. Read-only: signs in once per site,
// reads the landing page, and stops.
// usage: node live_versions.js <accounts.json> <accountKey> '{"PROJ":"https://..."}'
// The version regex comes from config "version_pattern" (env PMAUTO_VERSION_PATTERN); group 1 = the version.
// Always close our headless browser, even on error or timeout.
let __browser = null;
const __close = () => { try { __browser && __browser.close(); } catch (e) {} };
process.on('SIGTERM', () => { __close(); process.exit(1); });
process.on('SIGINT', () => { __close(); process.exit(1); });
const { loadPlaywright, launchOptions, account, signIn } = require('./browser');
const [, , accountsFile, accountKey, sitesJson] = process.argv;
const VERSION = process.env.PMAUTO_VERSION_PATTERN ? new RegExp(process.env.PMAUTO_VERSION_PATTERN)
  : /\bv(\d+\.\d+\.\d+(?:\.\d+)?)\b/;

(async () => {
  const { chromium } = loadPlaywright();
  const acct = account(accountsFile, accountKey);
  const browser = await chromium.launch(launchOptions()); __browser = browser;
  const out = {};
  for (const [project, url] of Object.entries(JSON.parse(sitesJson))) {
    const ctx = await browser.newContext(); const p = await ctx.newPage(); const r = { url };
    try {
      r.landed = await signIn(p, url, acct);
      const m = (await p.evaluate(() => document.body.innerText)).match(VERSION);
      r.version = m ? m[1] : null;
      if (!m) r.error = 'no version text found on the landing page';
    } catch (e) { r.error = e.message.split('\n')[0]; r.at = p.url().replace(/\?.*/, ''); }
    out[project] = r; await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(async e => { console.error(e.message); try { __browser && await __browser.close(); } catch (x) {} process.exit(1); });
