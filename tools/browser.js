// Shared helpers for the live-site tools: find the browser, read the test-account roster, sign in.
// Everything here is READ-ONLY: it navigates and reads; it never clicks Save/Submit/Approve.
const path = require('path'), fs = require('fs');

function loadPlaywright() {
  const candidates = [process.env.PLAYWRIGHT_CORE, path.join(__dirname, 'node_modules', 'playwright-core')];
  for (const c of candidates.filter(Boolean)) { try { return require(c); } catch (e) { /* try next */ } }
  throw new Error('playwright-core not found - run: python -m pmauto setup');
}

function launchOptions() {
  const exe = process.env.CHROME_EXE;
  if (exe) return { executablePath: exe };
  return { channel: process.env.PMAUTO_BROWSER_CHANNEL || undefined };
}

// Roster file: {"accounts": {"<key>": {"username": "...", "password": "...", "role": "...", "notes": [...]}}}.
// Field names are matched without regard to case.
function account(accountsFile, key) {
  const roster = JSON.parse(fs.readFileSync(accountsFile, 'utf8'));
  const a = (roster.accounts || {})[key];
  if (!a) throw new Error(`account '${key}' not in ${accountsFile}`);
  return a;
}

function field(acct, name) {
  const k = Object.keys(acct).find(x => x.toLowerCase() === name);
  return k === undefined ? undefined : acct[k];
}

// Sign-in selectors: config "login" (passed as PMAUTO_LOGIN JSON). The defaults fit most plain sign-in forms;
// stop_if_url (a regex) stops the run when sign-in lands on a page that must not be accepted automatically.
const LOGIN = Object.assign({
  username: "input[type=email], input[name=email], input[name=username], #username, #email",
  password: "input[type=password]",
  submit: "button[type=submit], input[type=submit]",
  stop_if_url: "",
}, JSON.parse(process.env.PMAUTO_LOGIN || '{}'));

async function signIn(page, url, acct) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  const email = page.locator(LOGIN.username).first();
  await email.waitFor({ state: 'visible', timeout: 30000 });
  await email.fill(field(acct, 'username'));
  await page.locator(LOGIN.password).first().fill(field(acct, 'password'));
  await page.locator(LOGIN.submit).first().click();
  await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
  const landed = page.url().replace(/\?.*/, '');
  if (LOGIN.stop_if_url && new RegExp(LOGIN.stop_if_url, 'i').test(landed)) throw new Error(`stopped at ${LOGIN.stop_if_url} page without accepting anything`);
  return landed;
}

module.exports = { loadPlaywright, launchOptions, account, signIn };
