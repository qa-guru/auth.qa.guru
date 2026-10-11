// Proves the P1 criterion "локальный вход по passkey" without a physical authenticator:
// Chrome DevTools Protocol exposes a virtual FIDO2 authenticator, so the whole passkey
// round-trip (register → sign in with no password) runs headless and repeatably.
//
//   node scripts/passkey-login-check.mjs            # reads dev/.env
//
// Also asserts the password fallback still works — a passwordless realm with no fallback is
// how you lose access along with the device (ADR 017 п. 8).

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const DEV_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function loadEnv() {
  const env = {};
  for (const line of readFileSync(resolve(DEV_DIR, ".env"), "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    env[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return env;
}

const env = loadEnv();
const BASE = `http://localhost:${env.KEYCLOAK_HTTP_PORT ?? "8543"}`;
const REALM = process.env.REALM ?? "qaguru";
const USER = process.env.STAND_USER ?? "student-demo";
const PASSWORD = env.STAND_USER_PASSWORD;
const ACCOUNT = `${BASE}/realms/${REALM}/account/`;
// RFC 7636 example verifier/challenge pair — public test vector, not a secret.
const PKCE = "code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";

const authUrl = (extra = "") =>
  `${BASE}/realms/${REALM}/protocol/openid-connect/auth?client_id=account-console` +
  `&redirect_uri=${encodeURIComponent(ACCOUNT)}&response_type=code&scope=openid&${PKCE}${extra}`;

const steps = [];
const record = (name, ok, detail) => {
  steps.push({ step: name, ok, detail });
  console.error(`${ok ? "ok   " : "FAIL "} ${name}${detail ? ` — ${detail}` : ""}`);
};

const loggedIn = async (page) => (await page.locator("#kc-form-login").count()) === 0;

// The account console is an SPA that fires its own auth request on load; without letting it
// settle, the next goto() races it and Chromium aborts the navigation.
const settle = async (page) => {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(300);
};

// Keycloak rejects a second credential with a label the user already has, so a repeat run would
// fail on the registration form. Clear the user's passkeys first and always test a fresh one.
async function dropExistingPasskeys() {
  const form = new URLSearchParams({
    client_id: "admin-cli",
    grant_type: "password",
    username: env.KC_BOOTSTRAP_ADMIN_USERNAME,
    password: env.KC_BOOTSTRAP_ADMIN_PASSWORD,
  });
  const tokenResp = await fetch(`${BASE}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: form,
  });
  const { access_token: token } = await tokenResp.json();
  const auth = { Authorization: `Bearer ${token}` };
  const admin = `${BASE}/admin/realms/${REALM}`;
  const [user] = await (await fetch(`${admin}/users?username=${USER}&exact=true`, { headers: auth })).json();
  if (!user) throw new Error(`user ${USER} not found — run scripts/seed-stand-users.py`);
  const credentials = await (await fetch(`${admin}/users/${user.id}/credentials`, { headers: auth })).json();
  let dropped = 0;
  for (const cred of credentials.filter((c) => c.type === "webauthn-passwordless")) {
    await fetch(`${admin}/users/${user.id}/credentials/${cred.id}`, { method: "DELETE", headers: auth });
    dropped += 1;
  }
  return dropped;
}

const presence = (cdp, authenticatorId, enabled) =>
  cdp.send("WebAuthn.setAutomaticPresenceSimulation", { authenticatorId, enabled });

async function residentSignCount(cdp, authenticatorId) {
  const { credentials } = await cdp.send("WebAuthn.getCredentials", { authenticatorId });
  const resident = credentials.find((c) => c.isResidentCredential);
  return resident ? resident.signCount : null;
}

async function main() {
  if (!PASSWORD) throw new Error("STAND_USER_PASSWORD missing in dev/.env");
  const dropped = await dropExistingPasskeys();
  if (dropped) console.error(`note  dropped ${dropped} existing passkey(s) of ${USER}`);

  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);

  await cdp.send("WebAuthn.enable", { enableUI: false });
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });

  try {
    // 1. Password login — the mandatory fallback.
    await page.goto(authUrl(), { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kc-form-login");
    await page.fill("#username", USER);
    await page.fill("#password", PASSWORD);
    await page.click("#kc-login");
    await page.waitForURL((url) => url.href.startsWith(ACCOUNT), { timeout: 15000 });
    const passwordLoginOk = await loggedIn(page);
    await settle(page);
    record("password login (fallback)", passwordLoginOk, USER);

    // 2. Register a passkey through the application-initiated action.
    page.on("dialog", (dialog) => dialog.accept("stand-virtual-authenticator"));
    await page.goto(authUrl("&kc_action=webauthn-register-passwordless"), {
      waitUntil: "domcontentloaded",
    });
    await page.waitForSelector("#registerWebAuthn");
    await page.click("#registerWebAuthn");
    await page.waitForURL((url) => url.href.startsWith(ACCOUNT), { timeout: 20000 });
    await settle(page);
    const signCountBefore = await residentSignCount(cdp, authenticatorId);
    record(
      "passkey registered",
      signCountBefore !== null,
      signCountBefore === null ? "no resident credential" : "1 resident credential",
    );

    // 3. Drop the session — the next login must stand on the passkey alone. Park on a blank
    // page first: the account console keeps polling its session and would hijack the next goto.
    await page.goto("about:blank");
    await context.clearCookies();

    // 4. Passkey login. The signature counter is the proof: it only moves when the credential
    // actually signs a WebAuthn assertion. Presence simulation stays off while the page loads,
    // otherwise conditional UI signs in before the form is even readable.
    await presence(cdp, authenticatorId, false);
    await page.goto(authUrl(), { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kc-form-login", { state: "attached" });
    const passwordFieldValue = await page.inputValue("#password");
    const button = page.locator("#authenticateWebAuthnButton");
    record("passkey button on login page", (await button.count()) === 1);

    await presence(cdp, authenticatorId, true);
    await button.click().catch(() => {});
    await page.waitForURL((url) => url.href.startsWith(ACCOUNT), { timeout: 20000 });
    const passkeyLoginOk = await loggedIn(page);
    const signCountAfter = await residentSignCount(cdp, authenticatorId);
    await settle(page);
    record(
      "passkey login (password field left empty)",
      passkeyLoginOk && passwordFieldValue === "" && signCountAfter > signCountBefore,
      `signCount ${signCountBefore} → ${signCountAfter}`,
    );

    // 5. GitHub brokering is wired on the same login page.
    await page.goto("about:blank");
    await context.clearCookies();
    await presence(cdp, authenticatorId, false);
    await page.goto(authUrl(), { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kc-form-login", { state: "attached" });
    const github = page.locator("#social-github");
    const href = (await github.count()) ? await github.getAttribute("href") : "";
    record("github brokering link", href.includes("/broker/github/login"), href.split("?")[0]);
  } finally {
    await browser.close();
  }

  const ok = steps.every((s) => s.ok);
  console.log(JSON.stringify({ ok, realm: REALM, user: USER, steps }, null, 2));
  if (!ok) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
