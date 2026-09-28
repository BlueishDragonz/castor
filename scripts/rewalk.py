"""Full-app rewalk: crawl every reachable route, measure design-system
consistency, and screenshot at desktop + mobile.

Usage: .venv/bin/python scripts/rewalk.py /tmp/rewalk

Writes /tmp/rewalk/report.json (measurements) and per-mode PNGs.
Reuses the CDP plumbing and credentials handling from ui-shots.py.
"""
import asyncio
import base64
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request

_s = importlib.util.spec_from_file_location("shots", "scripts/ui-shots.py")
sh = importlib.util.module_from_spec(_s)
_s.loader.exec_module(sh)

BASE = "http://127.0.0.1:4321"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/rewalk"
HEIGHT = 900
WIDTHS = {"desktop": 1440, "mobile": 390}

# Routes reachable without a valid entity id. Dynamic ones are resolved
# from the API after login.
SEED = [
    "/habits", "/stats", "/circles", "/settings", "/security", "/help",
    "/import", "/export", "/habits/new", "/habits/order", "/circles/new",
    "/login", "/register", "/forgot-password", "/privacy", "/terms",
    "/account/delete", "/admin", "/tokens",
]
# NOTE: /logout must NOT be crawled — it ends the session and every
# route after it is measured as signed-out. Visited separately last.

MEASURE = """
(() => {
  const px = v => Math.round(parseFloat(v) * 100) / 100;
  const cs = el => (el ? getComputedStyle(el) : null);
  const de = document.documentElement;

  const out = {
    url: location.pathname,
    title: (document.title || '').trim(),
    h1: [...document.querySelectorAll('h1')].map(
      e => e.textContent.trim().replace(/\\s+/g, ' ').slice(0, 60)),
    headings: [...document.querySelectorAll('h1,h2,h3,h4')].map(e => +e.tagName[1]),
    container: null, tokens: {}, spacing: {}, buttons: [],
    overflow: null, landmarks: {},
  };

  const rootStyle = cs(de);
  for (const t of ['--background', '--foreground', '--card', '--card-foreground',
                   '--muted', '--muted-foreground', '--primary', '--primary-foreground',
                   '--border', '--input', '--ring', '--control-border',
                   '--destructive', '--radius', '--shadow-sm', '--shadow-md']) {
    const v = rootStyle.getPropertyValue(t).trim();
    if (v) out.tokens[t] = v;
  }
  out.theme = de.getAttribute('data-theme') || '(unset)';
  out.bodyFont = cs(document.body) ? cs(document.body).fontFamily : null;
  out.bodyFontSize = cs(document.body) ? cs(document.body).fontSize : null;
  out.bodyBg = cs(document.body) ? cs(document.body).backgroundColor : null;
  out.bodyColor = cs(document.body) ? cs(document.body).color : null;

  const container = document.querySelector('main.castor-page')
    || document.querySelector('.castor-page');
  if (container) {
    const r = container.getBoundingClientRect();
    const s = cs(container);
    out.container = {
      sel: String(container.className).slice(0, 70),
      width: px(r.width), maxWidth: s.maxWidth,
      paddingLeft: s.paddingLeft, paddingRight: s.paddingRight,
      paddingTop: s.paddingTop, gap: s.gap,
    };
  }

  const m = document.querySelector('main');
  out.landmarks = {
    main: m ? (m.id || '(no id)') : 'MISSING',
    skipLink: !!document.querySelector('a[href="#main"]'),
    h1Count: document.querySelectorAll('h1').length,
    header: !!document.querySelector('header'),
    navs: document.querySelectorAll('nav').length,
  };

  for (const b of document.querySelectorAll('button, a[role=button]')) {
    const s2 = cs(b);
    const r = b.getBoundingClientRect();
    out.buttons.push({
      text: (b.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
      tag: b.tagName,
      disabled: b.disabled === true || b.getAttribute('aria-disabled') === 'true',
      h: px(r.height), w: px(r.width),
      bg: s2.backgroundColor, fg: s2.color, border: s2.borderColor,
      radius: s2.borderRadius,
    });
  }

  // Habit-grid geometry. The track ratios (--name-col vs --day-col) and
  // the column gap are tuned by eye, so they need a number to compare
  // against after a tweak — and `namesTruncating` is the check that
  // actually matters: it is the failure mode when the name loses its
  // share of the row to the date columns or to the tag badges.
  const grid = document.querySelector('.habit-row .habit-grid');
  if (grid) {
    const nameCell = document.querySelector('.habit-row .habit-grid__name');
    // Row cells carry the tick control, not .habit-grid__day (that class
    // is the header strip). Measure the cell that holds the switch.
    const days = [...document.querySelectorAll('.habit-row [role="gridcell"]')];
    const d0 = days[0] ? days[0].getBoundingClientRect() : null;
    const d1 = days[1] ? days[1].getBoundingClientRect() : null;
    const links = [...document.querySelectorAll('.habit-grid__link')];
    out.habitGrid = {
      gap: cs(grid).gap,
      nameCol: cs(grid).getPropertyValue('--name-col').trim(),
      nameCellW: nameCell ? px(nameCell.getBoundingClientRect().width) : null,
      dayW: d0 ? px(d0.width) : null,
      gapBetweenDays: (d0 && d1) ? px(d1.left - d0.right) : null,
      namesTruncating: links
        .filter(a => a.scrollWidth > a.clientWidth + 1)
        .map(a => a.textContent.trim()),
    };
  }

  out.overflow = { scrollW: de.scrollWidth, clientW: de.clientWidth,
                   bleeds: de.scrollWidth > de.clientWidth + 1 };
  if (out.overflow.bleeds) {
    out.overflow.offenders = [...document.querySelectorAll('body *')]
      .filter(e => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.right > de.clientWidth + 2;
      })
      .slice(0, 6)
      .map(e => ({ tag: e.tagName,
                   cls: String(e.className || '').slice(0, 60),
                   right: px(e.getBoundingClientRect().right) }));
  }

  const gaps = {};
  for (const e of document.querySelectorAll('main *')) {
    const s2 = cs(e);
    for (const prop of ['rowGap', 'columnGap']) {
      const v = s2[prop];
      if (v && v !== 'normal' && v !== '0px') gaps[v] = (gaps[v] || 0) + 1;
    }
  }
  out.spacing = gaps;

  return out;
})()
"""


async def main():
    port = 9500
    prof = "/tmp/castor-rewalk-prof"
    subprocess.run(["rm", "-rf", prof], check=False)
    proc = subprocess.Popen(
        [sh.CHROME, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", "--force-color-profile=srgb",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version")
                break
            except Exception:
                await asyncio.sleep(0.25)
        tabs = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
        pg = next(x for x in tabs if x["type"] == "page")
        tab = sh.Tab(pg["webSocketDebuggerUrl"])
        tab.send("Page.enable")
        tab.send("Runtime.enable")

        def js(expr):
            try:
                r = tab.send("Runtime.evaluate", expression=expr,
                             returnByValue=True, awaitPromise=True)
                return r["result"].get("value")
            except RuntimeError:
                return None

        def set_width(w):
            tab.send("Emulation.setDeviceMetricsOverride", width=w, height=HEIGHT,
                     deviceScaleFactor=1, mobile=(w < 500))

        # ---- log in while wide; changing metrics later drops the session ----
        set_width(1440)
        tab.send("Page.navigate", url=BASE + "/login")
        time.sleep(3)
        SET = ("(sel,val)=>{const e=document.querySelector(sel);"
               "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')"
               ".set.call(e,val);e.dispatchEvent(new Event('input',{bubbles:true}));"
               "return 1}")
        js(f"({SET})('input[type=email]','demo@castor.example.com')")
        js("document.querySelector('form button').click()")
        time.sleep(3)
        js(f"({SET})('input[type=password]','DemoPass1234!')")
        js("(()=>{const b=[...document.querySelectorAll('form button')]"
           ".find(x=>/sign in/i.test(x.textContent));b&&b.click();return 1})()")
        for _ in range(50):
            if js("location.pathname") != "/login":
                break
            time.sleep(0.4)
        print("login ->", js("location.pathname"), flush=True)

        # There is no BFF route for circles (the pages call the backend
        # directly), so resolve the id from the rendered /circles page
        # instead of guessing an API shape.
        tab.send("Page.navigate", url=BASE + "/circles")
        time.sleep(2.0)
        raw = js("""(async()=>{
          const r = await fetch('/api/v1/habits', {credentials:'same-origin'});
          const d = r.ok ? await r.json() : [];
          const list = Array.isArray(d) ? d : (d.habits || []);
          const hrefs = [...document.querySelectorAll('a[href^="/circles/"]')]
            .map(e => e.getAttribute('href'))
            .filter(h => h.split('/').length === 3 && h !== '/circles/new');
          const circle = hrefs.length ? hrefs[0].split('/').pop() : null;
          return JSON.stringify({habit: list[0] && list[0].id,
                                 circle: circle,
                                 nHabits: list.length});
        })()""")
        ids = json.loads(raw) if raw else {}
        print("resolved ids:", ids, flush=True)

        routes = list(SEED)
        if ids.get("habit"):
            h = ids["habit"]
            routes += [f"/habits/{h}", f"/habits/{h}/edit",
                       f"/habits/{h}/duplicate", f"/habits/{h}/archive",
                       f"/habits/{h}/complete"]
        if ids.get("circle"):
            c = ids["circle"]
            routes += [f"/circles/{c}", f"/circles/{c}/share",
                       f"/circles/{c}/join", f"/circles/{c}/leave",
                       f"/circles/{c}/invites", f"/circles/{c}/welcome",
                       f"/circles/{c}/delete"]
        routes += ["/404", "/nonexistent-page-xyz"]

        seen, ordered = set(), []
        for r in routes:
            if r not in seen:
                seen.add(r)
                ordered.append(r)

        results = {}
        for mode, w in WIDTHS.items():
            os.makedirs(f"{OUT}/{mode}", exist_ok=True)
            set_width(w)
            for route in ordered:
                key = f"{mode}:{route}"
                tab.send("Page.navigate", url=BASE + route)
                time.sleep(1.8)
                m = js(MEASURE)
                if m is None:
                    results[key] = {"error": "evaluate failed", "route": route}
                    print("  FAIL", key, flush=True)
                    continue
                m["route"] = route
                try:
                    shot = tab.send("Page.captureScreenshot", format="png",
                                    captureBeyondViewport=False)
                    name = route.strip("/").replace("/", "-") or "root"
                    open(f"{OUT}/{mode}/{name}.png", "wb").write(
                        base64.b64decode(shot["data"]))
                    m["shot"] = f"{mode}/{name}.png"
                except RuntimeError as exc:
                    m["shotError"] = str(exc)
                results[key] = m
            print(f"{mode}: captured {len(ordered)} routes", flush=True)

        json.dump(results, open(f"{OUT}/report.json", "w"), indent=1)
        print("wrote", f"{OUT}/report.json", flush=True)
        tab.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()


asyncio.run(main())
