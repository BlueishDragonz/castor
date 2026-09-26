// Verifies the Sentry privacy configuration (F11/F13) actually does what it
// claims, rather than trusting that the options were spelled correctly.
//
// The failure mode this guards against is specific and quiet: a config that
// looks right, reports errors usefully, and silently ships every user's email
// and reset token to a third party. Nothing crashes and nothing looks wrong.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');

let failures = 0;
function check(name, condition, detail = '') {
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    console.error(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`);
    failures += 1;
  }
}

console.log('Sentry configuration checks\n');

// --- Both SDKs are present, not just one -------------------------------
const astroConfig = readFileSync(resolve(root, 'astro.config.mjs'), 'utf8');
check(
  'astro.config.mjs registers @sentry/astro',
  /sentry\(\{/.test(astroConfig) && /@sentry\/astro/.test(astroConfig),
);

// The regression from the first attempt: gating the integration on the auth
// token silently disabled monitoring everywhere the token is absent.
check(
  'integration is NOT gated on SENTRY_AUTH_TOKEN',
  !/if\s*\(\s*sentryAuthToken\s*\)/.test(astroConfig),
  'gating the integration on the token disables runtime monitoring in every build without one',
);

// --- Client config ------------------------------------------------------
const client = readFileSync(resolve(root, 'sentry.client.config.js'), 'utf8');
check('client config sends no default PII', /sendDefaultPii:\s*false/.test(client));
check('client config disables session replay', /replaysSessionSampleRate:\s*0/.test(client));
for (const category of ['userInfo', 'headers', 'cookies', 'formData']) {
  check(
    `client dataCollection.${category} is false`,
    new RegExp(`${category}:\\s*false`).test(client),
  );
}
check('client drops URL query strings', /query:\s*false/.test(client));
check('client scrubs JWT-shaped values', /eyJ\[A-Za-z0-9_/.test(client));

// --- Server config is at least as strict --------------------------------
const server = readFileSync(resolve(root, 'sentry.server.config.js'), 'utf8');
check('server config sends no default PII', /sendDefaultPii:\s*false/.test(server));
for (const category of ['userInfo', 'headers', 'cookies', 'formData']) {
  check(
    `server dataCollection.${category} is false`,
    new RegExp(`${category}:\\s*false`).test(server),
  );
}
check('server discards the whole request object', /event\.request\s*=\s*\{/.test(server));
check('server drops URL query strings', /redactUrl/.test(server));
check(
  'server filters email/credential-ish extra keys',
  /email/.test(server) && /token/.test(server),
);

// --- Build output -------------------------------------------------------
// `find` exits 0 whether or not it matches, so match on output rather than
// on the exit code. An earlier version of this check inverted that and
// reported sourcemaps that did not exist.
try {
  const maps = execFileSync(
    'bash',
    ['-lc', `find ${resolve(root, 'dist')} -name '*.map' -type f`],
    { encoding: 'utf8' },
  )
    .trim()
    .split('\n')
    .filter(Boolean);
  check(
    'no sourcemaps shipped',
    maps.length === 0,
    `dist contains ${maps.length} .map file(s), so app source is public: ${maps.slice(0, 3).join(', ')}`,
  );
} catch (e) {
  check('no sourcemaps shipped', false, String(e));
}

try {
  const dsnHit = execFileSync(
    'bash',
    ['-lc', `grep -rl "194746b78af1d5c8a76844097430dad8" ${resolve(root, 'dist')} | wc -l`],
    { encoding: 'utf8' },
  ).trim();
  check(
    'Sentry DSN present in the build (monitoring is actually wired)',
    Number(dsnHit) > 0,
    'no DSN in dist means the SDK was never injected and nothing would report',
  );
} catch (e) {
  check('Sentry DSN present in the build', false, String(e));
}

console.log('');
if (failures > 0) {
  console.error(`${failures} check(s) failed.`);
  process.exit(1);
}
console.log('All Sentry configuration checks passed.');
