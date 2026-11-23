"""Exercise the shipped JALE Python entrypoint without depending on Android emulator startup."""
import http.client
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import types
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "android/app/src/main/python"))
sys.path.insert(0, str(ROOT / "integrations/all_by_devoll"))

# Android-specific PDF bridge is intentionally not invoked during this check.
java = types.ModuleType("java")
java.jclass = lambda name: None
sys.modules["java"] = java

import mobile_server

def request(path, cookie=None, body=None):
    con = http.client.HTTPConnection("127.0.0.1", 8765, timeout=3)
    headers = {"Host": "127.0.0.1:8765"}
    if cookie:
        headers["Cookie"] = "eg_session=" + cookie
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Origin"] = "http://127.0.0.1:8765"
    con.request("POST" if body is not None else "GET", path, body, headers)
    resp = con.getresponse()
    code = resp.status
    content_type = resp.getheader("Content-Type", "")
    data = resp.read()
    con.close()
    return code, content_type, data

with tempfile.TemporaryDirectory() as home:
    session = secrets.token_hex(32)
    assert mobile_server.start(home, session) == "started"
    for attempt in range(100):
        try:
            status, _, _ = request("/ready", session)
            if status == 200:
                break
        except (OSError, TimeoutError):
            pass
        time.sleep(0.1)
    else:
        raise AssertionError("JALE local server never started")
    assert request("/")[0] == 403, "unauthenticated local requests must be blocked"
    status, typ, page = request("/", session)
    assert status == 200 and b"<strong>JALE</strong>" in page, (status, typ)
    status, typ, logo = request("/brand/tomey.png", session)
    assert status == 200 and typ.startswith("image/png") and logo[:4] == bytes((137, 80, 78, 71)), (status, typ)
    customer = "JALE offline smoke " + secrets.token_hex(6)
    data = urlencode({"customer": customer, "problem": "Offline Android work", "service": "Mobile service"})
    status, _, _ = request("/ticket/new", session, data)
    assert status == 303, f"Cannot create local job: {status}"
    status, _, page = request("/", session)
    assert status == 200 and customer.encode() in page, "Created local job must appear on dashboard"
    assert request("/office")[0] == 403, "the sales and contract desk requires the private app session"
    status, _, office = request("/office", session)
    assert status == 200 and b"Office automation" in office and b"Forecast gap to $20,000" in office
    status, _, _ = request("/office/seed", session, "")
    assert status == 303, "the county call list must load via the private app server"
    status, _, office = request("/office", session)
    assert status == 200 and b"Copemish" in office and b"Skiera Management" in office
    import sqlite3
    with sqlite3.connect(Path(home) / "data" / "tickets.sqlite3") as con:
        lead_id = con.execute("SELECT id FROM office_accounts WHERE name='Skiera Management'").fetchone()[0]
    coach_path = f"/office/call-coach/{lead_id}"
    assert request(coach_path)[0] == 403
    status, _, coach = request(coach_path, session)
    assert status == 200 and b"+12315103071" in coach and b"Start live coaching" in coach
    status, typ, worklet = request("/office/call-assets/pcm.js", session)
    assert status == 200 and typ == "application/javascript" and b"registerProcessor" in worklet
    status, typ, routes = request("/office/routes.csv", session)
    assert status == 200 and typ.startswith("text/csv") and routes.startswith(b"date,time,route,")
    database = Path(home) / "data" / "tickets.sqlite3"
    assert database.is_file(), "SQLite database not persisted"
    print("PASS: real Android Python bootstrap, authenticated loopback, JALE dashboard, logo, job, office automation and SQLite")
