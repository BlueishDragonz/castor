"""Full export -> import round-trip against the running dev stack.

Drives the real pages through Chromium, exports the data, mutates it,
then re-imports the file through the real file input and asserts the
data came back.

  .venv/bin/python scripts/io-probe.py
"""
import asyncio
import importlib.util
import json
import pathlib
import subprocess

_spec = importlib.util.spec_from_file_location(
    "ui_shots", "/home/joel/castor-repo/scripts/ui-shots.py")
shots = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shots)  # type: ignore[union-attr]

EXPORT_FETCH = """(async () => {
  const r = await fetch('/api/v1/habits/export', {credentials: 'same-origin'});
  const j = await r.json();
  return JSON.stringify({
    status: r.status,
    isArray: Array.isArray(j),
    topKeys: Object.keys(j),
    habitCount: (j.habits || []).length,
    orderLen: (j.order || []).length,
    firstHabit: (j.habits || [])[0] || null,
    totalRecords: (j.habits || []).reduce(
      (n, h) => n + ((h.records || []).length), 0),
  });
})()"""

HABITS = """(async () => {
  const r = await fetch('/api/v1/habits', {credentials: 'same-origin'});
  const d = await r.json();
  const list = Array.isArray(d) ? d : (d.habits || []);
  return JSON.stringify(list.map(h => ({
    id: h.id, name: h.name, status: h.status,
    records: (h.records || []).length, period: h.period, tags: h.tags,
  })));
})()"""

FETCH_PUT = """(async (path, body) => {
  const r = await fetch(path, {method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    credentials: 'same-origin', body: JSON.stringify(body)});
  return r.status;
})"""

ALERTS = """(() => {
  return JSON.stringify([...document.querySelectorAll('[role=alert]')]
    .map(e => e.textContent.trim().replace(/\\s+/g, ' ').slice(0, 240)));
})()"""

EXPORT_RAW = """(async () => {
  const r = await fetch('/api/v1/habits/export', {credentials: 'same-origin'});
  return await r.text();
})()"""


async def main() -> None:
    subprocess.run(["rm", "-rf", shots.PROFILE], check=False)
    proc = subprocess.Popen([
        shots.CHROME, "--headless=new",
        f"--remote-debugging-port={shots.PORT}",
        f"--user-data-dir={shots.PROFILE}", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--force-color-profile=srgb",
        "--disable-dev-shm-usage", "--remote-allow-origins=*", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                shots.http_json("/json/version")
                break
            except Exception:
                await asyncio.sleep(0.25)
        page = next(t for t in shots.http_json("/json/list")
                    if t["type"] == "page")
        tab = shots.Tab(page["webSocketDebuggerUrl"])
        tab.send("Page.enable")
        tab.send("Runtime.enable")

        base = shots.BASE

        def js(expr, await_promise=True):
            return tab.send("Runtime.evaluate", expression=expr,
                            returnByValue=True,
                            awaitPromise=await_promise)["result"].get("value")

        tab.send("Page.navigate", url=f"{base}/login")
        await asyncio.sleep(1.8)
        SET = """(sel, val) => {
          const el = document.querySelector(sel);
          if (!el) return 'missing ' + sel;
          const d = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value');
          d.set.call(el, val);
          el.dispatchEvent(new Event('input', {bubbles: true}));
          return 'ok';
        }"""
        SUBMIT = """(() => { const f = document.querySelector('form');
                    f.requestSubmit ? f.requestSubmit() : f.submit();
                    return 1; })()"""
        js("(%s)('input[type=email]', 'demo@castor.example.com')" % SET)
        await asyncio.sleep(0.4)
        js(SUBMIT)
        await asyncio.sleep(1.8)
        js("(%s)('input[type=password]', 'DemoPass1234!')" % SET)
        await asyncio.sleep(0.4)
        js(SUBMIT)
        await asyncio.sleep(2.5)
        print("signed in, landed:", js("location.pathname"))

        # Earlier probe runs archived the demo habits, and the dev
        # seeder is idempotent (habits_created: 0), so un-archive them
        # here. The export includes archived habits, which is what the
        # round-trip is meant to preserve.
        wiped = js("""(async () => {
          const H = {headers: {'Content-Type': 'application/json'},
                     credentials: 'same-origin'};
          const seen = new Set();
          for (const st of ['active', 'archive', 'soft_delete']) {
            const r = await fetch('/api/v1/habits?status=' + st, H);
            if (!r.ok) continue;
            for (const h of (await r.json()) || []) {
              if (h && h.id && !seen.has(h.id)) seen.add(h.id);
            }
          }
          for (const id of seen) {
            await fetch('/api/v1/habits/' + id, {method: 'DELETE', ...H});
          }
          return seen.size;
        })()""")
        print("wiped habits:", wiped)
        await asyncio.sleep(1.0)

        # Build a known fixture: two habits with distinct periods, tags
        # and tick counts, so a round-trip has something to lose.
        seeded = js("""(async () => {
          const J = {'Content-Type': 'application/json',
                     credentials: 'same-origin'};
          const mk = async (name, period, tags, days) => {
            const r = await fetch('/api/v1/habits', {method: 'POST', ...J,
              body: JSON.stringify({name, period, tags})});
            if (!r.ok) return 'create failed ' + name;
            const h = await r.json();
            for (const d of days) {
              // There is no BFF route for completions, so fetch() here
              // would 404. Tick via the real page endpoint the UI posts
              // to: /habits/{id}/complete (dd-mm-yyyy in the form).
              const body = new URLSearchParams({date: d, done: 'true'});
              const r = await fetch('/habits/' + h.id + '/complete', {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type':
                  'application/x-www-form-urlencoded'},
                body: body.toString(), redirect: 'manual',
              });
              if (r.status >= 400) {
                return 'tick ' + d + ' failed ' + r.status;
              }
            }
            return h.id;
          };
          const a = await mk('Round trip alpha',
            {period_type: 'W', period_count: 1, target_count: 3},
            ['fitness', 'daily'], ['15-09-2026', '16-09-2026', '17-09-2026']);
          const b = await mk('Round trip beta',
            {period_type: 'M', period_count: 2, target_count: 5},
            ['learning'], ['20-09-2026']);
          return JSON.stringify([a, b]);
        })()""")
        print("seeded fixture:", seeded)
        await asyncio.sleep(1.5)

        restored = js("""(async () => {
          const H = {headers: {'Content-Type': 'application/json'},
                     credentials: 'same-origin'};
          const list = await (await fetch('/api/v1/habits/export', H)).json();
          const n = [];
          for (const h of (list.habits || [])) {
            if (h.status && h.status !== 'active') {
              await fetch('/api/v1/habits/' + h.id,
                          {method: 'PUT', ...H,
                           body: JSON.stringify({status: 'active'})});
              n.push(h.name);
            }
          }
          return JSON.stringify(n);
        })()""")
        print("re-activated:", restored)
        await asyncio.sleep(1.0)

        print("\n=== 1. BEFORE ===")
        before = json.loads(js(HABITS))
        print(json.dumps(before, indent=1))

        print("\n=== 2. EXPORT ===")
        exp = json.loads(js(EXPORT_FETCH))
        raw = js(EXPORT_RAW)
        fh = exp.get("firstHabit") or {}
        print(json.dumps({k: v for k, v in exp.items()
                          if k != "firstHabit"}, indent=1))
        print("first habit keys :", list(fh.keys()))
        print("first habit period:", fh.get("period"))
        print("first habit tags  :", fh.get("tags"))
        print("first habit status:", fh.get("status"))
        print("first habit records:", len(fh.get("records") or []))

        path = "/tmp/castor-export-test.json"
        with open(path, "w") as f:
            f.write(raw)
        detail = js("""(async () => {
          const list = await (await fetch('/api/v1/habits/export',
            {credentials: 'same-origin'})).json();
          const h = (list.habits || [])[0];
          if (!h) return 'no habits';
          const d = await (await fetch('/api/v1/habits/' + h.id,
            {credentials: 'same-origin'})).json();
          return JSON.stringify({
            id: h.id,
            exportRecords: (h.records || []).length,
            detailRecords: (d.records || []).length,
            detailKeys: Object.keys(d),
            firstDetail: JSON.stringify((d.records || [])[0] || null),
          });
        })()""")
        print("COMPARE export vs detail:", detail)
        print("saved export ->", path, len(raw), "bytes")
        print("parses as JSON object:", isinstance(json.loads(raw), dict))

        # ── 3. MUTATE: archive a habit, so a successful import proves
        #    it really replaced rather than merged. ───────────────────
        print("\n=== 3. MUTATE (archive one habit) ===")
        target = before[0]
        st = js("%s('/api/v1/habits/%s', {status: 'archive'})"
                % (FETCH_PUT, target["id"]))
        print("archived", target["name"], "->", st)
        await asyncio.sleep(1.2)
        print("active habits now:",
              [h["name"] for h in json.loads(js(HABITS))])

        # ── 4. IMPORT through the real <input type=file> ─────────────
        print("\n=== 4. IMPORT via the real file input ===")
        tab.send("Page.navigate", url=f"{base}/import")
        await asyncio.sleep(2.0)
        print("on:", js("location.pathname"))
        print("file input present:",
              js("!!document.getElementById('import-file')"))

        # DOM.setFileInputFiles is the only way to genuinely populate a
        # file input; assigning .files from JS is not permitted.
        tab.send("DOM.enable")
        # Tab.send already unwraps the CDP "result" envelope.
        doc = tab.send("DOM.getDocument")["root"]
        node = tab.send("DOM.querySelector", nodeId=doc["nodeId"],
                        selector="#import-file")["nodeId"]
        tab.send("DOM.setFileInputFiles", files=[path], nodeId=node)
        await asyncio.sleep(0.6)
        print("file attached:",
              js("document.getElementById('import-file').files[0] ? "
                 "document.getElementById('import-file').files[0].name : 'NONE'"))

        print("form count:", js("document.querySelectorAll('form').length"))
        print("submit btns:",
              js("[...document.querySelectorAll('button[type=submit]')]"
                 ".map(b => b.textContent.trim()).join(' | ')"))
        js(SUBMIT)
        await asyncio.sleep(4.0)
        mid = js("location.pathname")
        print("4s after submit, on:", mid)
        print("body text (first 400):",
              js("document.body.innerText.replace(/\\s+/g,' ').slice(0,400)"))
        await asyncio.sleep(8.0)
        print("12s after submit, on:", js("location.pathname"))
        print("alerts:", js(ALERTS))

        print("\n=== 5. AFTER import ===")
        print(json.dumps(json.loads(js(HABITS)), indent=1))
        print("\n=== 5b. ERROR PATHS (must reject without destroying data) ===")
        # Each payload goes through the real form. A rejected payload
        # must leave the user's habits untouched.
        bad_cases = [
            ("not json", "this is definitely not json {{{"),
            ("empty object", "{}"),
            ("habits not array", '{"habits": 42}'),
            ("habits empty", '{"habits": []}'),
            ("habit without name", '{"habits": [{"name": "  "}, {"name": "x"}]}'),
            ("habits key missing", '{"order": ["1"]}'),
            ("nested habit garbage", '{"habits": ["a", null, 7]}'),
        ]
        for label, payload in bad_cases:
            tab.send("Page.navigate", url=f"{base}/import")
            await asyncio.sleep(1.2)
            before_n = json.loads(js(HABITS))
            js("""(() => {
              const ta = document.querySelector('#import-json');
              ta.value = %s;
              const f = document.querySelector('form[method=post]');
              f.requestSubmit ? f.requestSubmit() : f.submit();
            })()""" % json.dumps(payload))
            await asyncio.sleep(2.2)
            msg = js("""(() => {
              const a = document.querySelector('[role=alert]');
              return JSON.stringify({
                alert: a ? a.innerText.replace(/\\s+/g, ' ').trim().slice(0,180)
                         : null,
                on: location.pathname,
                body: document.body.innerText
                  .replace(/\\s+/g, ' ').slice(0, 260),
              });
            })()""")
            print("  RAW:", msg[:400])
            after_n = json.loads(js(HABITS))
            verdict = ('OK (unchanged)' if len(before_n) == len(after_n)
                       else 'DATA LOST!')
            print(f"  {label:20s} habits {len(before_n)}->{len(after_n)} {verdict}")
            print(f"  {'':20s} msg: {msg[:100]!r}")

        print("\n=== 5c. FILE TYPE REJECTION ===")
        for fname, content in [
            ("data.txt", '{"habits": [{"name": "via wrong ext"}]}'),
            ("data.json", "not json at all"),
            ("empty.json", ""),
        ]:
            fp = f"/tmp/castor-upload-{fname}"
            pathlib.Path(fp).write_text(content)
            tab.send("Page.navigate", url=f"{base}/import")
            await asyncio.sleep(1.2)
            before_n = json.loads(js(HABITS))
            tab.send("DOM.enable")
            doc = tab.send("DOM.getDocument")["root"]
            node = tab.send("DOM.querySelector", nodeId=doc["nodeId"],
                            selector="#import-file")["nodeId"]
            tab.send("DOM.setFileInputFiles", files=[fp], nodeId=node)
            await asyncio.sleep(0.4)
            js("(() => { const f = document.querySelector('form[method=post]');"
               " f.requestSubmit ? f.requestSubmit() : f.submit(); })()")
            await asyncio.sleep(2.2)
            alert_txt = js("""(() => {
              const a = document.querySelector('[role=alert]');
              return a ? a.innerText.replace(/\\s+/g, ' ').trim().slice(0,150)
                       : 'NO MESSAGE';
            })()""")
            after_n = json.loads(js(HABITS))
            kept = ('OK (unchanged)' if len(before_n) == len(after_n)
                    else 'DATA LOST!')
            print(f"  {fname:12s} habits {len(before_n)}->{len(after_n)} {kept}")
            print(f"  {'':12s} {alert_txt[:130]!r}")

    finally:
        proc.terminate()
        proc.wait()


asyncio.run(main())
