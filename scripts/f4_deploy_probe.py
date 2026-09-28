#!/usr/bin/env python3
"""
F4 durability probe against the DEPLOYED Castör service.

Unit tests prove the storage layer behaves. They cannot prove the deployed
container actually flushes to disk before acknowledging a write, because they
never go through gunicorn, the lifespan shutdown hook, or a real SIGTERM.

This probe does, on the running production container:

  1. register a throwaway user and log in
  2. create a habit and complete it
  3. read it back through the API and compare
  4. read it back through SQLite, bypassing the API entirely
  5. (caller) SIGTERM the container, let it restart
  6. read it back again and require an identical answer
  7. delete the throwaway user via the F18 endpoint

Step 6 is the actual F4 assertion. A write that only lived in memory would
survive step 3 and fail step 6.

Usage:
  python3 f4_probe.py <phase> <base_url>
    phase "write"    steps 1-4, prints a token the caller reuses
    phase "verify"   step 6, compares against what "write" recorded
    phase "cleanup"  step 7
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = "https://castor.jota-rodrigues.com"
STATE = "/tmp/f4-probe-state.json"
EMAIL = "f4-probe-%s@example.com" % uuid.uuid4().hex[:10]
PASSWORD = uuid.uuid4().hex + "-Aa1!"


def call_form(method, path, fields, token=None, expect=None):
    """A form-encoded call. /auth/login is OAuth2PasswordRequestForm, which is
    x-www-form-urlencoded, NOT JSON: posting JSON yields a 422 that lists
    both `username` and `password` as missing, which reads like a wrong field
    name rather than a wrong content type."""
    import urllib.parse
    url = BASE + path
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw, status = r.read().decode(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    except Exception as e:
        return 0, "TRANSPORT: %s: %s" % (type(e).__name__, e)
    if expect is not None and status != expect:
        print("  !! %s %s -> %d (wanted %d)\n     %s" % (method, path, status, expect, raw[:300]))
    try:
        return status, json.loads(raw)
    except Exception:
        return status, raw


def call(method, path, body=None, token=None, expect=None):
    """One HTTP call. Never raises on an unexpected status: returns it."""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        status = e.code
    except Exception as e:
        return 0, "TRANSPORT: %s: %s" % (type(e).__name__, e)
    if expect is not None and status != expect:
        print("  !! %s %s -> %d (wanted %d)\n     %s" % (method, path, status, expect, raw[:300]))
    try:
        return status, json.loads(raw)
    except Exception:
        return status, raw


def read_off_disk(habit_id):
    """Read the habit's completion records straight from the SQLite file.

    Deliberately bypasses the API and the storage layer. The app caches habit
    lists in memory (F5), so a read through the app could be served from cache
    and would prove nothing. This reads the bytes on disk.

    Shape, read from the live database rather than assumed:

        habit_list.data -> {"habits": [{"id", "name", "tags",
                                        "records": [{"day","done","timestamp"}]}]}

    The whole list is one JSON blob on one row, which is precisely the thing
    F4 is about: a lost update here silently drops a user's record.
    """
    import sqlite3
    con = sqlite3.connect("/app/.user/habits.db")
    try:
        row = con.execute(
            "SELECT data FROM habit_list ORDER BY rowid DESC").fetchone()
    finally:
        con.close()
    if not row:
        return None
    payload = row[0]
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode()
    if isinstance(payload, str):
        payload = json.loads(payload)
    for habit in payload.get("habits", []):
        if str(habit.get("id")) == str(habit_id):
            # `timestamp` is wall-clock at write time, so it legitimately
            # differs between runs. The assertions are about `day` and `done`.
            return sorted(
                (r.get("day"), bool(r.get("done")))
                for r in habit.get("records", [])
            )
    return None


def do_write():
    print("1. register")
    st, _ = call("POST", "/auth/register",
                 {"email": EMAIL, "password": PASSWORD}, expect=201)
    if st == 429:
        print("  registration is capped on this deployment; cannot probe")
        return 1

    print("2. login")
    # The bearer transport posts OAuth2PasswordRequestForm, whose field is
    # `username`, not `email`. Sending `email` yields a 422 listing both
    # fields as missing.
    st, body = call_form("POST", "/auth/login",
                         {"username": EMAIL, "password": PASSWORD}, expect=200)
    if st != 200:
        print("  login failed: %s %s" % (st, body))
        return 1
    token = body["access_token"]

    print("3. create habit")
    st, habit = call("POST", "/api/v1/habits", {"name": "F4 probe"},
                     token=token, expect=200)
    if st != 200:
        print("  create failed: %s %s" % (st, habit))
        return 1
    hid = habit["id"]

    print("4. record a completion (this is the write under test)")
    # Tick.date is parsed with Tick.date_fmt, which defaults to %d-%m-%Y.
    # An ISO date is rejected with 400 "Invalid date format".
    today = __import__("datetime").datetime.now().strftime("%d-%m-%Y")
    st, _ = call("POST", "/api/v1/habits/%s/completions" % hid,
                 {"date": today, "done": True}, token=token, expect=200)
    if st != 200:
        print("  completion failed: %s" % st)
        return 1

    print("5. read back through the API")
    st, body = call("GET", "/api/v1/habits", token=token, expect=200)
    api_view = json.dumps(body, sort_keys=True)

    print("6. read back through SQLite, bypassing the API entirely")
    rows = read_off_disk(hid)

    state = {"email": EMAIL, "password": PASSWORD, "token": token,
             "habit_id": hid, "date": today, "api_view": api_view,
             "rows": rows}
    with open(STATE, "w") as f:
        json.dump(state, f)

    print("\n   habit_id      %s" % hid)
    print("   date          %s" % today)
    print("   on-disk rows  %s" % (rows,))
    if not rows:
        print("\n   RESULT: FAIL - the completion never reached disk")
        return 1
    print("\n   State recorded. Now SIGTERM the container, restart it, then:")
    print("     python3 f4_probe.py verify")
    return 0


def do_verify():
    with open(STATE) as f:
        s = json.load(f)
    print("re-authenticating as the probe user")
    st, body = call_form("POST", "/auth/login",
                         {"username": s["email"], "password": s["password"]}, expect=200)
    if st != 200:
        print("  cannot re-login: %s %s" % (st, body))
        return 1

    st, body = call("GET", "/api/v1/habits", token=body["access_token"], expect=200)
    now = json.dumps(body, sort_keys=True)

    rows = read_off_disk(s["habit_id"])

    print("\n   after restart:")
    print("     on-disk rows  %s" % (rows,))
    print("     expected      %s" % (s["rows"],))

    ok = True
    norm = lambda r: None if r is None else [list(x) for x in r]
    if norm(rows) != norm(s["rows"]):
        print("\n   RESULT: FAIL - the write did not survive the restart")
        ok = False
    if now != s["api_view"]:
        print("   RESULT: FAIL - the API view changed across the restart")
        ok = False
    if ok:
        print("\n   RESULT: PASS - the write survived a full SIGTERM and restart.")
        print("   F4 is validated on the deployed service.")
    return 0 if ok else 1


def do_cleanup():
    try:
        with open(STATE) as f:
            s = json.load(f)
    except FileNotFoundError:
        print("no probe state; nothing to clean up")
        return 0
    st, body = call_form("POST", "/auth/login",
                         {"username": s["email"], "password": s["password"]}, expect=200)
    if st != 200:
        print("cannot log in to clean up: %s" % st)
        return 1
    # F18 requires the account password in the body.
    st, body = call("DELETE", "/api/v1/account",
                    {"password": s["password"]},
                    token=body["access_token"], expect=204)
    print("cleanup delete -> %s %s" % (st, body))
    os.path.exists(STATE) and os.remove(STATE)
    return 0 if st == 204 else 1


def do_negative():
    """Negative control for the probe itself.

    Rewrites the recorded expectation to something the disk does not contain
    and confirms the verify step notices. A durability test that has never
    been observed to fail is indistinguishable from one that cannot.
    """
    with open(STATE) as f:
        s = json.load(f)
    real = read_off_disk(s["habit_id"])
    print("   real on-disk:  %s" % ([list(x) for x in real],))
    s["rows"] = [["1999-01-01", True]]
    with open(STATE, "w") as f:
        json.dump(s, f)
    print("   planted:       [['1999-01-01', True]]")
    rc = do_verify()
    print("\n   negative control exit=%d (want 1)" % rc)
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    phase = sys.argv[1]
    if len(sys.argv) > 2:
        BASE = sys.argv[2].rstrip("/")
    sys.exit({"write": do_write, "verify": do_verify,
              "cleanup": do_cleanup, "negative": do_negative}[phase]())
