// Create the Keycloak brokering OAuth App in a GitHub org.
//
// GitHub exposes no REST API for OAuth Apps — not creation, not editing — so this
// is the one step in phase 11.qa-guru-identity that cannot be a plain HTTP call.
// It drives the real Chrome profile instead, because the session lives in cookies
// encrypted against the macOS Keychain and does not survive a profile copy.
//
// Chrome must be fully quit: Chrome locks its user data dir.
// The client secret is written to a 600 file and never printed.
//
//   node scripts/create-github-oauth-app.mjs [--org qa-guru] [--out /tmp/p5-gh-app.json]

import { chromium } from '../node_modules/playwright/index.mjs';
import { writeFileSync, existsSync, chmodSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};

const ORG = arg('org', 'qa-guru');
const OUT = arg('out', join(homedir(), '.config/auth-qa-guru/github-oauth-broker.json'));
const AUTH_URL = arg('auth-url', 'https://auth.qa.guru');
const REALM = arg('realm', 'qaguru');
const APP_NAME = arg('name', 'Keycloak — auth.qa.guru');
const CALLBACK = `${AUTH_URL}/realms/${REALM}/broker/github/endpoint`;
const PROFILE = join(homedir(), 'Library/Application Support/Google/Chrome');

if (!existsSync(PROFILE)) {
  console.error(`no Chrome profile at ${PROFILE}`);
  process.exit(1);
}

const log = (m) => console.log(`  ${m}`);

async function readClientId(page) {
  const fromAttr = await page.locator('[data-clipboard-text]').evaluateAll((els) =>
    els.map((el) => el.getAttribute('data-clipboard-text')).filter(Boolean)
  );
  const fromCode = await page.locator('code').evaluateAll((els) => els.map((el) => el.textContent.trim()));
  const candidates = [...fromAttr, ...fromCode].filter((v) => /^Ov23li[A-Za-z0-9]+$/.test(v) || /^[0-9a-f]{20}$/.test(v));
  if (!candidates.length) throw new Error('could not find a Client ID on the app page');
  return candidates[0];
}

async function readClientSecret(page) {
  const fromAttr = await page.locator('[data-clipboard-text]').evaluateAll((els) =>
    els.map((el) => el.getAttribute('data-clipboard-text')).filter(Boolean)
  );
  const fromCode = await page.locator('code').evaluateAll((els) => els.map((el) => el.textContent.trim()));
  const candidates = [...fromAttr, ...fromCode].filter(
    (v) => /^[0-9a-f]{40}$/.test(v) || /^[A-Za-z0-9_/+-]{32,}$/.test(v)
  );
  const secret = candidates.find((v) => !/^Ov23li/.test(v) && !/^[0-9a-f]{20}$/.test(v));
  if (!secret) throw new Error('secret was not rendered — generate it by hand in the open window');
  return secret;
}

// GitHub interrupts sensitive actions with a "Confirm access" sudo page. Only the
// human can clear it, so wait for them rather than failing the run.
async function clearSudo(page, timeoutMs = 300000) {
  const deadline = Date.now() + timeoutMs;
  let announced = false;
  while (Date.now() < deadline) {
    const url = page.url();
    const isSudo = url.includes('/sessions/sudo') || /github\.com\/login(\?|$)/.test(url);
    if (!isSudo) return true;
    if (!announced) {
      log('>>> GitHub is asking you to confirm access in the open window. Do it there; I keep waiting.');
      announced = true;
    }
    await page.waitForTimeout(2000);
  }
  return false;
}

const ctx = await chromium.launchPersistentContext(PROFILE, {
  channel: 'chrome',
  headless: false,
  viewport: null,
  args: ['--no-first-run', '--no-default-browser-check', '--profile-directory=Default'],
});

let result = null;
try {
  const page = ctx.pages()[0] || (await ctx.newPage());

  log(`opening the new-OAuth-App form for org ${ORG}`);
  await page.goto(`https://github.com/organizations/${ORG}/settings/applications/new`, {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await clearSudo(page);

  if (page.url().includes('/login')) throw new Error('Chrome profile is not signed in to GitHub');

  await page.locator('input[name="oauth_application[name]"]').waitFor({ timeout: 30000 });
  await page.fill('input[name="oauth_application[name]"]', APP_NAME);
  await page.fill('input[name="oauth_application[url]"]', AUTH_URL);
  const callbackField = page.locator('input[name="oauth_application[callback_url]"]').first();
  await callbackField.fill(CALLBACK);
  log(`filled: ${APP_NAME} | ${AUTH_URL} | ${CALLBACK}`);

  await Promise.all([
    page.waitForLoadState('domcontentloaded', { timeout: 60000 }),
    page.getByRole('button', { name: /Register application/i }).click(),
  ]);
  await clearSudo(page);
  log(`registered → ${page.url()}`);

  const clientId = await readClientId(page);
  log(`client id ${clientId}`);

  await page.getByRole('button', { name: /Generate a new client secret/i }).click({ timeout: 30000 });
  await page.waitForLoadState('domcontentloaded', { timeout: 60000 });
  await clearSudo(page);

  const clientSecret = await readClientSecret(page);
  result = { client_id: clientId, client_secret: clientSecret, callback: CALLBACK, app_url: page.url() };
} finally {
  if (result) {
    mkdirSync(join(homedir(), '.config/auth-qa-guru'), { recursive: true, mode: 0o700 });
    writeFileSync(OUT, JSON.stringify(result, null, 2) + '\n', { mode: 0o600 });
    chmodSync(OUT, 0o600);
    log(`wrote ${OUT} (600) — client id ${result.client_id}, secret not printed`);
  }
  await ctx.close();
}

if (!result) process.exit(1);
