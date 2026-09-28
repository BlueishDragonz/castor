// Sentry server-side SDK — F13 monitoring.
//
// This runs inside the Astro SSR process, i.e. in the same trust boundary as
// the application and its database. Server-side capture is where a sensitive
// value is MOST likely to be captured by accident: a handler that logs
// `user.email` inside a thrown error, or a stack frame whose locals include a
// request body.
//
// So the server config is stricter than the client one. It keeps the stack
// trace and the release — the parts that make an error actionable — and drops
// everything that could carry a person or a credential.
import * as Sentry from '@sentry/astro';

const dsn =
  import.meta.env.PUBLIC_SENTRY_DSN ||
  'https://194746b78af1d5c8a76844097430dad8@o4512052468514816.ingest.de.sentry.io/4512154770407504';

Sentry.init({
  dsn,

  // F11: never attach request bodies, headers, cookies or IP to an event
  // from a server that talks to a database of personal data.
  sendDefaultPii: false,

  dataCollection: {
    userInfo: false,
    headers: false,
    cookies: false,
    formData: false,
    url: { query: false, fragment: false },
  },

  beforeSend(event) {
    if (event.request) {
      // The request object on a server event is the most dangerous field in
      // the payload: it is the whole inbound HTTP request.
      event.request = {
        url: redactUrl(event.request.url),
        method: event.request.method,
      };
      event.request.data = undefined;
      event.request.cookies = undefined;
      event.request.headers = undefined;
    }

    if (event.user) {
      event.user = { id: undefined };
    }
    if (event.server_name) {
      // The hostname is infrastructure detail, not needed to debug.
      event.server_name = undefined;
    }
    if (event.extra) {
      for (const key of Object.keys(event.extra)) {
        if (/(pass|token|secret|auth|credential|cookie|jwt|bearer|email|body)/i.test(key)) {
          event.extra[key] = '[Filtered]';
        }
      }
    }
    return event;
  },
});

/** Strip query string and fragment, which can carry reset tokens. */
function redactUrl(url) {
  if (typeof url !== 'string') return url;
  const base = url.split('?')[0].split('#')[0];
  return base;
}
