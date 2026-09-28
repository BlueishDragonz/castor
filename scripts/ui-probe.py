#!/usr/bin/env python3
"""
ui-probe.py — measure the rendered DOM at a given viewport.

Same CDP approach as ui-shots.py, but reports computed geometry instead
of pixels. Use it to check claims (alignment, overflow, contrast,
target sizes) with numbers rather than eyeballing a screenshot.

Usage:
    .venv/bin/python scripts/ui-probe.py mobile
    .venv/bin/python scripts/ui-probe.py desktop
"""

import asyncio
import importlib.util
import json
import subprocess
import sys
import urllib.request

_spec = importlib.util.spec_from_file_location("shots", "scripts/ui-shots.py")
assert _spec is not None and _spec.loader is not None
shots = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shots)

VIEWPORTS = {"desktop": shots.DESKTOP, "mobile": shots.MOBILE}

MEASURE = """(() => {
  const q = s => document.querySelector(s);
  const box = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return {x: Math.round(b.x), y: Math.round(b.y),
            w: Math.round(b.width), h: Math.round(b.height)}; };
  const out = { url: location.pathname, vw: innerWidth,
                docScrollW: document.documentElement.scrollWidth };

  out.hOverflow = out.docScrollW > out.vw + 1;
  out.main = box(q('main.castor-page, main'));

  const panel = q('.habit-panel');
  if (panel) {
    const p = panel.getBoundingClientRect();
    const m = out.main;
    out.panel = {w: Math.round(p.width), x: Math.round(p.x),
                 mainX: m ? m.x : null, mainW: m ? m.w : null,
                 centreDelta: m
                   ? Math.round((p.x + p.width / 2) - (m.x + m.w / 2))
                   : null,
                 centred: m
                   ? Math.abs((p.x + p.width / 2) - (m.x + m.w / 2)) < 2
                   : null};
    const link = q('.habit-grid__link');
    if (link) {
      out.habitName = {text: link.textContent.trim(),
                       w: Math.round(link.getBoundingClientRect().width),
                       truncated: link.scrollWidth > link.clientWidth + 1};
    }
    const hd = document.querySelectorAll('.habit-grid--head .habit-grid__day');
    const rc = document.querySelectorAll('.habit-row:first-child .habit-grid__cell');
    let maxDelta = 0;
    hd.forEach((h, i) => {
      if (rc[i]) maxDelta = Math.max(maxDelta,
        Math.abs(h.getBoundingClientRect().x - rc[i].getBoundingClientRect().x));
    });
    out.headerRowMisalignPx = Math.round(maxDelta);
    if (rc.length) {
      const last = rc[rc.length - 1].getBoundingClientRect();
      out.lastDateCell = {right: Math.round(last.right),
                          visible: last.right <= innerWidth};
    }
    const hint = q('.habit-panel__scroll-hint');
    out.scrollHintVisible = hint ? getComputedStyle(hint).display !== 'none' : false;
  }

  const tick = q('.tick');
  if (tick) { const b = tick.getBoundingClientRect();
    out.tick = {w: Math.round(b.width), h: Math.round(b.height)}; }

  const hdr = q('.page-header');
  if (hdr) {
    const h1 = hdr.querySelector('h1');
    const sub = hdr.querySelector('.page-header__subtitle');
    if (h1 && sub) {
      out.subtitleAlignsTitle =
        Math.abs(h1.getBoundingClientRect().x - sub.getBoundingClientRect().x) < 1;
    }
  }

  const nav = q('.app-header__nav');
  out.topNav = nav ? getComputedStyle(nav).display : 'absent';
  out.bottomNav = (() => { const n = q('nav.bottom-nav');
    return n ? getComputedStyle(n).display : 'absent'; })();
  out.activeNav = q('.app-header__link[aria-current=page]')?.textContent?.trim() ?? null;
  return JSON.stringify(out);
})()"""


async def probe(name):
    vp = VIEWPORTS[name]
    port = 9345
    proc = subprocess.Popen([
        shots.CHROME, "--headless=new",
        f"--remote-debugging-port={port}",
        "--user-data-dir=/tmp/castor-probe", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--remote-allow-origins=*", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version")
                break
            except Exception:
                await asyncio.sleep(0.25)
        targets = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/list"))
        page = next(t for t in targets if t["type"] == "page")
        tab = shots.Tab(page["webSocketDebuggerUrl"])
        tab.send("Page.enable")
        tab.send("Runtime.enable")
        tab.send("Emulation.setDeviceMetricsOverride", **vp)

        def js(expr):
            try:
                r = tab.send("Runtime.evaluate", expression=expr,
                             returnByValue=True, awaitPromise=True)
                return r["result"].get("value")
            except RuntimeError as e:
                return f"<nav: {e}>"

        SET = ("(sel,val)=>{const el=document.querySelector(sel);"
               "if(!el)return 'miss';"
               "Object.getOwnPropertyDescriptor("
               "HTMLInputElement.prototype,'value').set.call(el,val);"
               "el.dispatchEvent(new Event('input',{bubbles:true}));"
               "return 'ok';}")

        tab.send("Page.navigate", url=f"{shots.BASE}/login")
        await asyncio.sleep(1.8)
        js(f"({SET})('input[type=email]','demo@castor.example.com')")
        js("document.querySelector('form button')?.click(),'c'")
        await asyncio.sleep(2.2)
        js(f"({SET})('input[type=password]','DemoPass1234!')")
        js("(()=>{const b=[...document.querySelectorAll('form button')]"
           ".find(x=>/sign in/i.test(x.textContent));b?.click();return 'c'})()")
        await asyncio.sleep(2.8)
        js("(()=>{const b=[...document.querySelectorAll('button')]"
           ".find(x=>/maybe later/i.test(x.textContent));b?.click();return 'd'})()")
        await asyncio.sleep(2.0)
        print(f"[{name}] auth -> {js('location.pathname')}")

        for route in ("/habits", "/stats", "/settings", "/circles"):
            tab.send("Page.navigate", url=shots.BASE + route)
            await asyncio.sleep(1.8)
            raw = js(MEASURE)
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                print(f"  {route:12s} -> {raw}")
                continue
            print(f"  {route}")
            for k, v in data.items():
                if k == "main":
                    continue
                print(f"      {k}: {v}")
        tab.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    for which in (sys.argv[1:] or ["desktop", "mobile"]):
        asyncio.run(probe(which))
        print()
