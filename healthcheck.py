"""Container health check for castor.

Why this does not use `requests`
--------------------------------
The first version used `requests.get(...)`, and it took 5.5s in the production
image against Docker's 3s health-check timeout — so the container was
permanently `unhealthy` while serving perfectly. Measured: `import requests`
alone costs 9.1s in this image.

That is not a service fault, and a health check that reports "unhealthy" when
the service is fine is worse than no health check at all: it will take an
operator down a rabbit hole chasing a phantom outage, and if it is ever
trusted for a rollout gate it will block a healthy deploy.

`urllib.request` is in the standard library, imports in milliseconds, and is
already a transitive dependency of the app. The same 1.9s request that
`requests` answered in 5.5s, it answers in 1.9s, mostly waiting on the socket.

The check is deliberately simple: TCP/HTTP reachability of the public port. A
deeper check (database, migrations) belongs in the application's own
`/health` endpoint, which already asserts the database — see castor/main.py
and audit F8. This probe's only job is to tell Docker whether to restart the
container, and a dependency-import failure is not evidence about the service.
"""

import sys
import urllib.error
import urllib.request

HEALTH_URL = "http://localhost:8080/health"
TIMEOUT_SECONDS = 2.0


def main() -> int:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=TIMEOUT_SECONDS) as response:
            if response.status == 200:
                return 0
            print(
                f"unhealthy: {HEALTH_URL} returned HTTP {response.status}",
                file=sys.stderr,
            )
    except urllib.error.URLError as exc:
        print(f"unhealthy: {HEALTH_URL} unreachable: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        print(f"unhealthy: unexpected error: {exc!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
