"""Sales follow-ups, contracted services and billing in the existing shop ledger."""
import calendar
import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit

import customer_payments
import job_calendar
import stock

DDL = """
CREATE TABLE IF NOT EXISTS office_preferences (
 name TEXT PRIMARY KEY,
 value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS office_accounts (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 account_key TEXT NOT NULL UNIQUE,
 phone TEXT NOT NULL,
 phone_key TEXT NOT NULL,
 email TEXT NOT NULL,
 area TEXT NOT NULL,
 website TEXT NOT NULL,
 stage TEXT NOT NULL DEFAULT 'new',
 follow_up TEXT NOT NULL,
 owner TEXT NOT NULL,
 pitch TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS office_account_phone ON office_accounts(phone_key);
CREATE TABLE IF NOT EXISTS office_calls (
 id INTEGER PRIMARY KEY,
 account_id INTEGER NOT NULL REFERENCES office_accounts(id),
 happened_at TEXT NOT NULL,
 outcome TEXT NOT NULL,
 notes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS office_intake (
 ticket_id INTEGER PRIMARY KEY REFERENCES tickets(id),
 account_id INTEGER REFERENCES office_accounts(id)
);
CREATE TABLE IF NOT EXISTS office_plans (
 id INTEGER PRIMARY KEY,
 request TEXT NOT NULL UNIQUE,
 account_id INTEGER NOT NULL REFERENCES office_accounts(id),
 site TEXT NOT NULL,
 route TEXT NOT NULL,
 service TEXT NOT NULL,
 scope TEXT NOT NULL,
 cadence TEXT NOT NULL,
 start_on TEXT NOT NULL,
 end_on TEXT NOT NULL,
 clock TEXT NOT NULL,
 monthly_cents INTEGER NOT NULL CHECK(monthly_cents > 0),
 trigger_inches INTEGER,
 next_on TEXT
);
CREATE TABLE IF NOT EXISTS office_stops (
 plan_id INTEGER NOT NULL REFERENCES office_plans(id),
 day TEXT NOT NULL,
 ticket_id INTEGER NOT NULL UNIQUE REFERENCES tickets(id),
 state TEXT NOT NULL CHECK(state IN ('scheduled','done','skipped')),
 PRIMARY KEY(plan_id, day)
);
CREATE TABLE IF NOT EXISTS office_invoices (
 account_id INTEGER NOT NULL REFERENCES office_accounts(id),
 month TEXT NOT NULL,
 ticket_id INTEGER NOT NULL UNIQUE REFERENCES tickets(id),
 due_on TEXT NOT NULL,
 PRIMARY KEY(account_id, month)
);
CREATE TABLE IF NOT EXISTS office_invoice_lines (
 ticket_id INTEGER NOT NULL REFERENCES office_invoices(ticket_id),
 plan_id INTEGER NOT NULL REFERENCES office_plans(id),
 description TEXT NOT NULL,
 cents INTEGER NOT NULL,
 PRIMARY KEY(ticket_id, plan_id)
);
"""

STAGES = ("new", "contacted", "quote", "signed", "closed")
CADENCES = ("weekly", "biweekly", "monthly", "snow")
SERVICES = ("Trash pickup", "Scrap pickup", "Snow removal", "Property service")
DEFAULT_TARGET_MONTH = "2027-02"
MONTHLY_TARGET = 2_000_000
VISIT_HORIZON = 7


def today():
    return job_calendar.today()


def text(data, key, label, required=True, limit=200):
    return stock.text(data.get(key, ""), label, limit=limit, required=required)


def day(value):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() == value and 2000 <= result.year <= 9998:
            return result
    except ValueError:
        pass
    raise ValueError("Enter a valid date.")


def money(cents):
    return format(Decimal(cents) / 100, ".2f")


def cents(value):
    return int(customer_payments.money(value) * 100)


def phone_key(phone):
    digits = re.sub(r"[^0-9]", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def next_month(month):
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def parse_month(value, current):
    month = job_calendar.parse_month(value, current)
    return month if 2000 <= month.year <= 9998 else current.replace(day=1)


def shared_origin(value):
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::[0-9]{1,5})?", value):
        raise ValueError("Enter the shared desk’s HTTPS address without a path.")
    url = urlsplit(value)
    if url.port == 0:
        raise ValueError("Enter a valid HTTPS port.")
    # Browsers normalize host casing and omit the default HTTPS port from Origin.
    return "https://" + url.hostname.lower() + (f":{url.port}" if url.port not in (None, 443) else "")


def save_shared_desk(con, value):
    con.execute("""INSERT INTO office_preferences(name,value) VALUES ('shared_desk',?)
        ON CONFLICT(name) DO UPDATE SET value=excluded.value""", (shared_origin(value),))


def month_day(month, number):
    return month.replace(day=min(number, calendar.monthrange(month.year, month.month)[1]))


def active_month(first):
    return first.isoformat(), (next_month(first) - timedelta(days=1)).isoformat()


def account(con, account_id):
    return stock.lookup(con, "SELECT * FROM office_accounts WHERE id=?", (account_id,), "Account")


def save_account(con, data, *, update=False, default_day=None):
    name = text(data, "name", "account name")
    area = text(data, "area", "area", False)
    phone = text(data, "phone", "phone", False, 40)
    email = text(data, "email", "email", False)
    website = text(data, "website", "website", False, 350)
    if website:
        try:
            url = urlsplit(website)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError
        except ValueError:
            raise ValueError("Enter a valid HTTPS website.") from None
    owner = text(data, "owner", "sales owner", False)
    pitch = text(data, "pitch", "service to pitch", False, 300)
    key = " ".join(f"{name} {area}".casefold().split())
    if update:
        current = account(con, stock.identifier(data.get("account_id", "")))
        duplicate = con.execute("SELECT id FROM office_accounts WHERE account_key=? AND id<>?",
                                (key, current["id"])).fetchone()
        if duplicate:
            raise ValueError("An account with this name and area already exists.")
        con.execute("""UPDATE office_accounts SET name=?,account_key=?,phone=?,phone_key=?,
                       email=?,area=?,website=?,owner=?,pitch=? WHERE id=?""",
                    (name, key, phone, phone_key(phone), email, area, website, owner, pitch, current["id"]))
        return current["id"]
    now = default_day or today()
    follow_up = day(data["follow_up"]) if data.get("follow_up") else now
    con.execute("""INSERT INTO office_accounts
        (name,account_key,phone,phone_key,email,area,website,follow_up,owner,pitch)
        VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_key) DO NOTHING""",
        (name, key, phone, phone_key(phone), email, area, website, follow_up.isoformat(), owner, pitch))
    return con.execute("SELECT id FROM office_accounts WHERE account_key=?", (key,)).fetchone()["id"]


def import_leads(con, content):
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
    allowed = {"name", "phone", "email", "area", "website", "owner", "follow_up", "pitch"}
    try:
        headers = reader.fieldnames or []
        if "name" not in headers or len(set(headers)) != len(headers) or set(headers) - allowed:
            raise ValueError("Use name,phone,email,area,website,owner,follow_up,pitch as CSV columns (only name is required).")
        count = 0
        for item in reader:
            if None in item or any(value is None for value in item.values()):
                raise ValueError(f"CSV row {reader.line_num} has the wrong number of fields.")
            previous = con.total_changes
            save_account(con, item)
            count += con.total_changes - previous
        return count
    except csv.Error as error:
        raise ValueError(f"Invalid CSV near row {reader.line_num}.") from error


def log_call(con, data, now, current=None):
    current = current or today()
    account_id = stock.identifier(data.get("account_id", ""))
    account(con, account_id)
    stage = data.get("stage")
    if stage not in STAGES:
        raise ValueError("Choose a lead status.")
    notes = text({**data, "notes": " ".join(data.get("notes", "").splitlines())}, "notes", "call notes", True, 500)
    follow_up = "" if stage in ("signed", "closed") else (
        day(data["follow_up"]) if data.get("follow_up") else current + timedelta(days=3)).isoformat()
    con.execute("INSERT INTO office_calls(account_id,happened_at,outcome,notes) VALUES (?,?,?,?)",
                (account_id, now, stage, notes))
    con.execute("UPDATE office_accounts SET stage=?,follow_up=? WHERE id=?", (stage, follow_up, account_id))


def link_intake(con, ticket_id, phone):
    key = phone_key(phone)
    matches = [] if not key else con.execute(
        "SELECT id FROM office_accounts WHERE phone_key=? LIMIT 2", (key,)).fetchall()
    con.execute("INSERT OR IGNORE INTO office_intake(ticket_id,account_id) VALUES (?,?)",
                (ticket_id, matches[0]["id"] if len(matches) == 1 else None))


def set_intake_account(con, data):
    ticket_id = stock.identifier(data.get("ticket_id", ""))
    account_id = stock.identifier(data.get("account_id", ""))
    account(con, account_id)
    stock.lookup(con, "SELECT id FROM tickets WHERE id=?", (ticket_id,), "Job")
    if protected_kind(con, ticket_id):
        raise ValueError("This record already belongs to a service agreement.")
    con.execute("""INSERT INTO office_intake(ticket_id,account_id) VALUES (?,?)
        ON CONFLICT(ticket_id) DO UPDATE SET account_id=excluded.account_id""", (ticket_id, account_id))


def add_plan(con, data, current=None):
    account_id = stock.identifier(data.get("account_id", ""))
    account(con, account_id)
    request = data.get("request", "")
    if not re.fullmatch(r"[0-9a-f]{32}", request):
        raise ValueError("Reload the agreement form before saving.")
    if con.execute("SELECT 1 FROM office_plans WHERE request=?", (request,)).fetchone():
        return account_id
    site = text(data, "site", "service address")
    route = text(data, "route", "route")
    scope = text(data, "scope", "signed scope", True, 500)
    service = data.get("service")
    cadence = data.get("cadence")
    if cadence not in CADENCES or service not in SERVICES or ((cadence == "snow") != (service == "Snow removal")):
        raise ValueError("Choose a schedule that matches the service.")
    start = day(data.get("start_on", ""))
    end = day(data["end_on"]) if data.get("end_on") else None
    if start < (current or today()).replace(day=1):
        raise ValueError("New agreements must start in the current month or later.")
    if (end and end < start) or (cadence == "snow" and not end):
        raise ValueError("Enter an end date on or after the start. Snow agreements require an end date.")
    clock = data.get("clock", "")
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", clock):
        raise ValueError("Enter a scheduled local time.")
    trigger = None
    if cadence == "snow":
        trigger = snowfall(data.get("trigger", ""))
        if not trigger:
            raise ValueError("Set a positive snowfall trigger.")
    charge = cents(data.get("monthly", ""))
    con.execute("""INSERT INTO office_plans
        (request,account_id,site,route,service,scope,cadence,start_on,end_on,clock,
         monthly_cents,trigger_inches,next_on) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (request, account_id, site, route, service, scope, cadence, start.isoformat(),
         end.isoformat() if end else "", clock, charge, trigger, start.isoformat() if cadence != "snow" else None))
    con.execute("UPDATE office_accounts SET stage='signed',follow_up='' WHERE id=?", (account_id,))
    return account_id


def end_plan(con, data, now, current):
    plan_id = stock.identifier(data.get("plan_id", ""))
    plan = stock.lookup(con, "SELECT * FROM office_plans WHERE id=?", (plan_id,), "Agreement")
    end = day(data.get("end_on", ""))
    if end < current or end.isoformat() < plan["start_on"] or (plan["end_on"] and end.isoformat() > plan["end_on"]):
        raise ValueError("Choose an end date from today through the current agreement end.")
    con.execute("UPDATE office_plans SET end_on=? WHERE id=?", (end.isoformat(), plan_id))
    rows = con.execute("""SELECT ticket_id FROM office_stops WHERE plan_id=? AND day>? AND state='scheduled'""",
                       (plan_id, end.isoformat())).fetchall()
    for row in rows:
        close_stop(con, {"ticket_id": str(row["ticket_id"]), "state": "skipped"}, now)
    return plan["account_id"]


def snowfall(value):
    if not re.fullmatch(r"[0-9]{1,2}(?:\.[0-9]{1,2})?", value):
        raise ValueError("Enter measured snowfall in inches (up to two decimal places).")
    return int(Decimal(value) * 100)


def make_ticket(con, source, now, customer, phone, site, service, scope, estimate_cents=0):
    job = con.execute("""INSERT INTO tickets
        (source_id,received_at,updated_at,customer,phone,location,service,problem,extra_charge,status)
        VALUES (?,?,?,?,?,?,?,?,?,'approved')""",
        (source, now, now, customer, phone, site, service, scope, money(estimate_cents)))
    con.execute("INSERT INTO events(ticket_id,timestamp,event) VALUES (?,?,'received')", (job.lastrowid, now))
    return job.lastrowid


def schedule_stop(con, plan, visit, now):
    iso = visit.isoformat()
    if con.execute("SELECT 1 FROM office_stops WHERE plan_id=? AND day=?", (plan["id"], iso)).fetchone():
        return
    ticket_id = make_ticket(con, f"office:stop:{plan['id']}:{iso}", now, plan["name"], plan["phone"],
                            plan["site"], plan["service"], plan["scope"])
    con.execute("INSERT INTO office_stops VALUES (?,?,?,'scheduled')", (plan["id"], iso, ticket_id))
    job_calendar.schedule(con, ticket_id, iso, plan["clock"])


def dispatch_snow(con, data, now, current=None):
    current = current or today()
    inches = snowfall(data.get("inches", ""))
    route = text(data, "route", "snow route")
    plans = con.execute("""SELECT p.*,a.name,a.phone FROM office_plans p JOIN office_accounts a ON a.id=p.account_id
        WHERE p.cadence='snow' AND p.route=? COLLATE NOCASE AND p.start_on<=? AND p.end_on>=?
        AND p.trigger_inches<=?""", (route, current.isoformat(), current.isoformat(), inches))
    for plan in plans.fetchall():
        schedule_stop(con, plan, current, now)


def close_stop(con, data, now):
    ticket_id = stock.identifier(data.get("ticket_id", ""))
    state = data.get("state")
    if state not in ("done", "skipped"):
        raise ValueError("Choose completed or skipped.")
    updated = con.execute("UPDATE office_stops SET state=? WHERE ticket_id=? AND state='scheduled'", (state, ticket_id))
    if not updated.rowcount:
        return
    if state == "done":
        con.execute("""UPDATE tickets SET status='completed', bill_total='0.00', finalized_at=?,updated_at=?
                       WHERE id=?""", (now, now, ticket_id))
    else:
        con.execute("UPDATE tickets SET status='cancelled',updated_at=? WHERE id=?", (now, ticket_id))
    con.execute("INSERT INTO events(ticket_id,timestamp,event) VALUES (?,?,?)",
                (ticket_id, now, "contract_stop_" + state))


def generate_stops(con, now, current):
    horizon = current + timedelta(days=VISIT_HORIZON)
    plans = con.execute("""SELECT p.*,a.name,a.phone FROM office_plans p JOIN office_accounts a ON a.id=p.account_id
        WHERE next_on<=? AND (end_on='' OR next_on<=end_on)""", (horizon.isoformat(),)).fetchall()
    for plan in plans:
        visit = day(plan["next_on"])
        last = min(horizon, day(plan["end_on"])) if plan["end_on"] else horizon
        while visit <= last:
            schedule_stop(con, plan, visit, now)
            if plan["cadence"] == "monthly":
                visit = month_day(next_month(visit), day(plan["start_on"]).day)
            else:
                visit += timedelta(days=7 if plan["cadence"] == "weekly" else 14)
        con.execute("UPDATE office_plans SET next_on=? WHERE id=?", (visit.isoformat(), plan["id"]))


def billable_plans(con, first):
    month_start, month_end = active_month(first)
    return con.execute("""SELECT p.*,a.name,a.phone FROM office_plans p JOIN office_accounts a ON a.id=p.account_id
        WHERE p.start_on<=? AND (p.end_on='' OR p.end_on>=?) ORDER BY p.account_id,p.id""",
        (month_end, month_start)).fetchall()


def generate_invoices(con, now, current):
    earliest = con.execute("SELECT min(start_on) FROM office_plans").fetchone()[0]
    if not earliest:
        return
    first = day(earliest).replace(day=1)
    cutoff = current.replace(day=1)
    while first < cutoff:
        month = first.isoformat()[:7]
        missing = con.execute("""SELECT DISTINCT p.account_id FROM office_plans p
            WHERE p.start_on<=? AND (p.end_on='' OR p.end_on>=?)
            AND NOT EXISTS(SELECT 1 FROM office_invoices i WHERE i.account_id=p.account_id AND i.month=?)""",
            (active_month(first)[1], first.isoformat(), month)).fetchall()
        missing_ids = {row["account_id"] for row in missing}
        if missing_ids:
            grouped = {}
            for plan in billable_plans(con, first):
                if plan["account_id"] in missing_ids:
                    grouped.setdefault(plan["account_id"], []).append(plan)
            for account_id, plans in grouped.items():
                draft_invoice(con, account_id, month, next_month(first), plans, now)
        first = next_month(first)


def draft_invoice(con, account_id, month, issue_day, plans, now):
    descriptions = [(p["id"], f"{p['service']} at {p['site']}", p["monthly_cents"]) for p in plans]
    total = sum(item[2] for item in descriptions)
    due = issue_day + timedelta(days=30)
    scope = f"Service month {month}. Payment due {due:%Y-%m-%d}."
    lines = "\n".join(f"{name} (${money(price)})" for _, name, price in descriptions)
    ticket_id = make_ticket(con, f"office:invoice:{account_id}:{month}", now, plans[0]["name"], plans[0]["phone"],
                            "", "Monthly service agreement", scope, total)
    con.execute("UPDATE tickets SET notes=? WHERE id=?", (scope + "\n" + lines, ticket_id))
    con.execute("INSERT INTO office_invoices VALUES (?,?,?,?)", (account_id, month, ticket_id, due.isoformat()))
    con.executemany("INSERT INTO office_invoice_lines VALUES (?,?,?,?)",
                    ((ticket_id, plan_id, name, price) for plan_id, name, price in descriptions))


def run(con, now, current=None):
    current = current or today()
    generate_stops(con, now, current)
    generate_invoices(con, now, current)


def protected_kind(con, ticket_id):
    if con.execute("SELECT 1 FROM office_stops WHERE ticket_id=?", (ticket_id,)).fetchone():
        return "stop"
    if con.execute("SELECT 1 FROM office_invoices WHERE ticket_id=?", (ticket_id,)).fetchone():
        return "invoice"
    return None


def forecast(con, first, amounts, local_day):
    last = next_month(first) - timedelta(days=1)
    by_account = {}
    for plan in billable_plans(con, first):
        account_id = plan["account_id"]
        by_account[account_id] = by_account.get(account_id, 0) + plan["monthly_cents"]
    recurring = sum(by_account.values())
    # Monthly agreement invoices are attributed to the service month. Visit
    # tickets are already covered by that price and are never counted twice.
    jobs = con.execute("""SELECT t.*, a.appointment_date FROM tickets t
        LEFT JOIN job_appointments a ON a.ticket_id=t.id
        WHERE t.status NOT IN ('cancelled') AND
        NOT EXISTS (SELECT 1 FROM office_stops o WHERE o.ticket_id=t.id) AND
        NOT EXISTS (SELECT 1 FROM office_invoices i WHERE i.ticket_id=t.id)
        AND (t.finalized_at IS NOT NULL OR (t.status IN ('approved','in_progress')
             AND a.appointment_date BETWEEN ? AND ?))""", (first.isoformat(), last.isoformat()))
    booked = 0
    billed_one_off = 0
    for job in jobs:
        if job["finalized_at"]:
            finalized = local_day(job["finalized_at"])
            if finalized and first <= finalized <= last:
                billed_one_off += int(amounts(job)["basis"] * 100)
        else:
            booked += int(amounts(job)["basis"] * 100)
    billed_contracts = sum(int(Decimal(r[0]) * 100) for r in con.execute("""SELECT t.bill_total
        FROM office_invoices i JOIN tickets t ON t.id=i.ticket_id WHERE i.month=? AND t.finalized_at IS NOT NULL""",
        (first.isoformat()[:7],)))
    paid = sum(int(Decimal(r[0]) * 100) for r in con.execute("""SELECT applied FROM customer_payments
        WHERE voided_at IS NULL AND paid_on BETWEEN ? AND ?""", (first.isoformat(), last.isoformat())))
    split = sum(int((Decimal(total) * Decimal("0.15")).quantize(Decimal(1), rounding=ROUND_HALF_UP))
                for total in by_account.values())
    return dict(recurring=recurring, booked=booked, billed_one_off=billed_one_off,
                billed_contracts=billed_contracts, paid=paid, split=int(split))


def draft_estimate(con, data, now):
    ticket_id = stock.identifier(data.get("ticket_id", ""))
    row = stock.lookup(con, "SELECT * FROM tickets WHERE id=?", (ticket_id,), "Job")
    if row["finalized_at"] or row["status"] == "cancelled" or protected_kind(con, ticket_id):
        raise ValueError("Choose an open one-off job.")
    values = [text(data, key, key.replace("_", " "), True, 30) for key in
              ("labor_hours", "labor_rate", "truck", "disposal")]
    costs = []
    for value in values:
        if not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", value):
            raise ValueError("Enter confirmed hours and costs, including zero if a cost does not apply.")
        costs.append(Decimal(value))
    hours, rate, truck, disposal = costs
    if hours > 10000:
        raise ValueError("Enter no more than 10,000 estimated labor hours.")
    cost = hours * rate + truck + disposal + Decimal(row["parts_cost"] or "0")
    # The displayed price must actually cover cost after the rounded owner share.
    minimum = (cost / Decimal("0.85")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if minimum - (minimum * Decimal("0.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) < cost:
        minimum += Decimal("0.01")
    summary = f"Internal estimate. Labor: {hours} h × ${rate:.2f}. Truck: ${truck:.2f}. Disposal: ${disposal:.2f}."
    summary += f" Parts cost: ${Decimal(row['parts_cost'] or '0'):.2f}. Minimum after 15% split: ${minimum:.2f}."
    con.execute("INSERT INTO events(ticket_id,timestamp,event,detail) VALUES (?,?,'office_estimate',?)",
                (ticket_id, now, summary))
    return ticket_id, minimum


def approve_invoice(con, data, now):
    ticket_id = stock.identifier(data.get("ticket_id", ""))
    stock.lookup(con, "SELECT ticket_id FROM office_invoices WHERE ticket_id=?", (ticket_id,), "Monthly invoice")
    invoice = con.execute("SELECT extra_charge FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    result = con.execute("""UPDATE tickets SET bill_total=?, finalized_at=?, status='completed', updated_at=?
        WHERE id=? AND finalized_at IS NULL""", (invoice["extra_charge"], now, now, ticket_id))
    if result.rowcount:
        con.execute("INSERT INTO events(ticket_id,timestamp,event,detail) VALUES (?,?,'bill_finalized',?)",
                    (ticket_id, now, invoice["extra_charge"]))
    return ticket_id


def update_intake(con, data, now):
    ticket_id = stock.identifier(data.get("ticket_id", ""))
    row = stock.lookup(con, "SELECT * FROM tickets WHERE id=?", (ticket_id,), "Job")
    if row["finalized_at"] or row["status"] == "cancelled" or protected_kind(con, ticket_id):
        raise ValueError("Choose an open intake job.")
    values = [text(data, key, key, False, 4000) for key in ("customer", "phone", "location", "service", "problem")]
    con.execute("UPDATE tickets SET customer=?,phone=?,location=?,service=?,problem=?,updated_at=? WHERE id=?",
                (*values, now, ticket_id))
    con.execute("INSERT INTO events(ticket_id,timestamp,event) VALUES (?,?,'intake_updated')", (ticket_id, now))
    con.execute("DELETE FROM office_intake WHERE ticket_id=? AND account_id IS NULL", (ticket_id,))
    link_intake(con, ticket_id, values[1])
    return ticket_id
