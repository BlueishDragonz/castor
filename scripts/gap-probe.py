"""Identify which elements produce the off-scale 6/7/10px gaps."""
import asyncio
import importlib.util
import json
import subprocess
import time
import urllib.request

_s = importlib.util.spec_from_file_location("shots", "scripts/ui-shots.py")
sh = importlib.util.module_from_spec(_s)
_s.loader.exec_module(sh)

BASE = "http://127.0.0.1:4321"
PAGES = ["/habits", "/habits/new", "/stats", "/circles", "/login"]

PROBE = """
(() => {
  const hits = [];
  for (const e of document.querySelectorAll('main *, main')) {
    const s = getComputedStyle(e);
    for (const p of ['rowGap', 'columnGap']) {
      const v = s[p];
      if (v && v !== 'normal' && v !== '0px') {
        hits.push({ gap: v, prop: p, tag: e.tagName,
          cls: String(e.className || '').slice(0, 70) });
      }
    }
  }
  return JSON.stringify(hits);
})()
"""


async def main():
    port = 9600
    prof = "/tmp/castor-gap-prof"
    subprocess.run(["rm", "-rf", prof], check=False)
    proc = subprocess.Popen(
        [sh.CHROME, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", "--remote-allow-origins=*", "about:blank"],
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

        tab.send("Emulation.setDeviceMetricsOverride", width=1440, height=900,
                 deviceScaleFactor=1, mobile=False)
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

        for page in PAGES:
            tab.send("Page.navigate", url=BASE + page)
            time.sleep(2.0)
            hits = json.loads(js(PROBE) or "[]")
            odd = [h for h in hits if h["gap"] in ("6px", "7px", "10px")]
            print(f"\n=== {page} ===")
            seen = set()
            for h in odd:
                k = (h["gap"], h["cls"])
                if k in seen:
                    continue
                seen.add(k)
                print(f"  {h['gap']:5s} {h['prop']:9s} <{h['tag']}> "
                      f".{h['cls'][:62]}")
        tab.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()


asyncio.run(main())
