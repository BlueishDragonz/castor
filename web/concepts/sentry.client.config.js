// Sentry browser SDK — F13 monitoring.
//
// The DSN is a PUBLIC ingest key: it identifies the project and is not a
// secret. It is safe in client code, and the server rejects events that do not
// carry a valid release, so it cannot be used to pollute the project from an
// arbitrary origin.
//
// F11/F13 privacy posture: this app deals in habit data, passwords and
// recovery codes. The defaults send user IP, request bodies and URL query
// strings to a third party. Those are switched off here rather than left
// implicit, so the decision is visible in review instead of buried in a
// dependency's default. What remains is what makes a stack trace useful:
// the error, the stack, the release and the environment.
import * as Sentry from '@sentry/astro';

const dsn =
  import.meta.env.PUBLIC_SENTRY_DSN ||
  'https://194746b78af1d5c8a76844097430dad8@o4512052468514816.ingest.de.sentry.io/4512154770407504';

Sentry.init({
  dsn,
  // Do not ship the session replay payload builder. There is no replay
  // integration configured, so this is belt and braces against a future
  // `replaysSessionSampleRate` being added by accident on a page that renders
  // a user's habits.
  replaysSessionSampleRate: 0,
  replaysOnErrorSampleRate: 0,

  // F11: no personal data leaves the instance by default.
  sendDefaultPii: false,

  // Drop the noisy categories entirely rather than relying on the key
  // denylist: the denylist is a blocklist, and a new endpoint named after
  // something benign would pass straight through it.
  dataCollection: {
    userInfo: false,
    headers: false,
    cookies: false,
    formData: false,
    // Query strings can contain a password-reset token or an email address.
    url: { query: false, fragment: false },
  },

  // Strip anything that looks like a credential before it leaves the browser,
  // including from breadcrumb messages and error messages, where a value can
  // be interpolated by accident.
  beforeSend(event) {
    return scrubEvent(event);
  },
});

/** Redact credential-shaped values from every string Sentry might carry. */
function scrubEvent(event) {
  const SENSITIVE = /(pass(word)?|token|secret|auth|credential|cookie|jwt|bearer)/i;
  const REDACTED = '[Filtered]';

  const scrubString = (value) => {
    if (typeof value !== 'string') return value;
    // JWT-shaped and long bearer-ish strings, regardless of the key they
    // arrived under.
    if (/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./.test(value)) return REDACTED;
    if (/\b[A-Fa-f0-9]{64}\b/.test(value)) return REDACTED; // sha256 / secret
    return value;
  };

  if (event.request) {
    event.request.data = scrubString(event.request.data);
    event.request.url = scrubString(event.request.url);
    event.request.cookies = undefined;
    event.request.headers = undefined;
  }
  if (event.breadcrumbs) {
    for (const crumb of event.breadcrumbs) {
      crumb.message = scrubString(crumb.message);
      if (crumb.data) {
        for (const [key, value] of Object.entries(crumb.data)) {
          if (SENSITIVE.test(key)) crumb.data[key] = REDACTED;
          else if (typeof value === 'string') crumb.data[key] = scrubString(value);
        }
      }
    }
  }
  if (event.extra) {
    for (const [key, value] of Object.entries(event.extra)) {
      if (SENSITIVE.test(key)) event.extra[key] = REDACTED;
    }
  }
  return event;
}
