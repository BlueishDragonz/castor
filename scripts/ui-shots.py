#!/usr/bin/env python3
"""
ui-shots.py — headless screenshot harness for the Castor Astro UI.

Drives the Playwright-managed Chromium over the DevTools Protocol with
no extra Python deps (raw websocket via the stdlib). Logs in once,
reuses the session cookie, then captures each route at desktop and
mobile widths so visual review covers both.

Usage:
    python3 scripts/ui-shots.py <outdir> [desktop|mobile|both]

Routes are the signed-in product surfaces plus the signed-out landing.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import websocket  # websocket-client

CHROME = os.path.expanduser(
    "~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
)
BASE = "http://localhost:4321"
PORT = 9333
PROFILE = "/tmp/castor-ui-shots-profile"

DESKTOP = {"width": 1440, "height": 900, "deviceScaleFactor": 1,
           "mobile": False}
MOBILE = {"width": 390, "height": 844, "deviceScaleFactor": 2,
          "mobile": True}

# (slug, path, needs_scroll_full)
ROUTES = [
    ("landing", "/", False),
    ("login", "/login", False),
    ("register", "/register", False),
    ("habits", "/habits", True),
    ("habits-new", "/habits/new", True),
    ("habit-detail", "/habits", True),
    ("habits-order", "/habits/order", True),
    ("stats", "/stats", True),
    ("circles", "/circles", True),
    ("circles-new", "/circles/new", True),
    ("settings", "/settings", True),
    ("security", "/security", True),
    ("import", "/import", True),
    ("export", "/export", True),
    ("help", "/help", True),
]


def http_json(path, payload=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))


class Tab:
    """Minimal CDP client over one target's websocket."""

    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0

    def send(self, method, **params):
        self._id += 1
        self.ws.send(json.dumps(
            {"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            # else: event, ignore

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


async def wait_for_path(tab, js, prefixes, want_absent=False,
                        password_ready=False, timeout=25.0):
    """Poll the page until it settles on a decisive state.

    Replaces fixed sleeps in the auth sequence. A cold Vite start can
    take several seconds to compile a route, and a fixed interval meant
    the script typed a password into a page that had not navigated yet
    — which surfaced as "login failed - session not established" even
    though the credentials were correct.

    want_absent=True  -> wait until the path is NOT under any prefix
    password_ready    -> wait for a password input to be present
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        where = js("location.pathname")
        if where and not isinstance(where, str):
            where = None
        under = bool(where) and any(str(where).startswith(p) for p in prefixes)
        if password_ready:
            ready = js("!!document.querySelector('input[type=password]')")
            if under and ready:
                return str(where)
        else:
            if where and not under:
                return str(where)
        await asyncio.sleep(0.4)
    return js("location.pathname")


async def shoot_all(outdir, modes):
    os.makedirs(outdir, exist_ok=True)
    subprocess.run(["rm", "-rf", PROFILE], check=False)
    proc = subprocess.Popen([
        CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--force-color-profile=srgb",
        "--disable-dev-shm-usage",
        "--remote-allow-origins=*", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # wait for CDP
        for _ in range(80):
            try:
                version = http_json("/json/version")
                break
            except Exception:
                await asyncio.sleep(0.25)
        else:
            raise RuntimeError("chrome did not expose CDP")

        targets = http_json("/json/list")
        page = next(t for t in targets if t["type"] == "page")
        tab = Tab(page["webSocketDebuggerUrl"])
        tab.send("Page.enable")
        tab.send("Runtime.enable")
        tab.send("Network.enable")

        # ── Authenticate once; the session cookie persists in the
        # profile for every later navigation. Each step is its own
        # Runtime.evaluate: a form submit tears down the execution
        # context, so an awaited script spanning the click would error
        # with "Inspected target navigated or closed".
        tab.send("Page.navigate", url=f"{BASE}/login")
        await asyncio.sleep(1.6)

        def js(expr, await_promise=False):
            # await_promise is required for any expression returning a
            # promise — without it Runtime.evaluate hands back the
            # unresolved Promise object and returnByValue yields None.
            try:
                return tab.send("Runtime.evaluate", expression=expr,
                                returnByValue=True,
                                awaitPromise=await_promise)["result"].get("value")
            except RuntimeError as e:
                return f"<nav: {e}>"

        SET_VALUE = """
          (sel, val) => {
            const el = document.querySelector(sel);
            if (!el) return 'missing ' + sel;
            const proto = el instanceof HTMLTextAreaElement
              ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            return 'ok';
          }"""

        js(f"({SET_VALUE})('input[type=email]', 'demo@castor.example.com')")
        js("document.querySelector('form button[type=submit], "
           "form button')?.click(), 'clicked'")
        # Wait for the password step to actually render rather than
        # sleeping a fixed interval: on a cold Vite start the first
        # compile can take several seconds, and a fixed 2s wait left the
        # script typing into a page that had not navigated yet.
        await wait_for_path(tab, js, ("/login",), password_ready=True)

        js(f"({SET_VALUE})('input[type=password]', 'DemoPass1234!')")
        js("(() => { const b = Array.from("
           "document.querySelectorAll('form button'))"
           ".find(x => /sign in/i.test(x.textContent));"
           "b?.click(); return 'clicked'; })()")
        # Sign-in redirects to /habits on success, or back to /login
        # with ?error=bad_request on failure.
        await wait_for_path(tab, js, ("/login",), want_absent=True)

        # post-login passkey offer -> dismiss
        js("(() => { const b = Array.from(document.querySelectorAll('button'))"
           ".find(x => /maybe later/i.test(x.textContent));"
           "if (b) { b.click(); return 'dismissed'; } return 'no offer'; })()")
        await asyncio.sleep(1.8)
        where = js("location.pathname")
        print(f"  [auth] landed on {where}", flush=True)
        if not where or str(where).startswith("/login"):
            raise RuntimeError("login failed - session not established")

        results = []
        for mode_name, vp in (("desktop", DESKTOP), ("mobile", MOBILE)):
            if mode_name not in modes:
                continue
            tab.send("Emulation.setDeviceMetricsOverride", **vp)

            # Resolve a real habit id from the API rather than hardcoding
            # one: demo ids change whenever the seed is regenerated, and
            # a stale id silently 302s to /login, which made the
            # "habit-detail" shot a duplicate of the login page.
            tab.send("Page.navigate", url=f"{BASE}/habits")
            await asyncio.sleep(1.4)
            habit_id = js("""(async () => {
              const r = await fetch('/api/v1/habits', {credentials:'same-origin'});
              if (!r.ok) return null;
              const d = await r.json();
              return d.length ? d[0].id : null;
            })()""", await_promise=True)
            await asyncio.sleep(0.4)
            if not habit_id:
                print("  [warn] no habit id resolved; "
                      "habit-detail will fall back to /habits", flush=True)
                habit_id = None

            for slug, path, full in ROUTES:
                if slug == "habit-detail" and habit_id:
                    path = f"/habits/{habit_id}"
                url = BASE + path
                tab.send("Page.navigate", url=url)
                await asyncio.sleep(1.4)
                # settle fonts + any client islands
                tab.send("Runtime.evaluate", expression="""
                  (async () => {
                    await document.fonts.ready;
                    await new Promise(r => setTimeout(r, 450));
                    return true;
                  })()""", awaitPromise=True)
                m = tab.send("Page.getLayoutMetrics")
                css_h = m.get("cssContentSize", m["contentSize"])["height"]
                full_h = min(int(css_h) + 20, 6000)
                shot = tab.send("Page.captureScreenshot", format="png",
                                captureBeyondViewport=bool(full),
                                clip={"x": 0, "y": 0,
                                      "width": vp["width"],
                                      "height": full_h,
                                      "scale": 1})
                out = os.path.join(outdir, f"{mode_name}-{slug}.png")
                with open(out, "wb") as f:
                    f.write(base64.b64decode(shot["data"]))
                # capture console errors too
                results.append({"mode": mode_name, "slug": slug,
                                "path": path, "file": out,
                                "height": full_h})
                print(f"  {mode_name:8s} {slug:16s} {path:24s} "
                      f"h={full_h}", flush=True)
        tab.close()
        return results
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    outdir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/castor-shots"
    modes = sys.argv[2] if len(sys.argv) > 2 else "both"
    requested = set(modes.split(","))
    if "both" in requested or not ({"desktop", "mobile"} & requested):
        modes = {"desktop", "mobile"}
    else:
        modes = requested
    asyncio.run(shoot_all(outdir, modes))
    print(f"\nwrote screenshots to {outdir}")
