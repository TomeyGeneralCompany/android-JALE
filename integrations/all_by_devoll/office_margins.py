"""Actual service-stop costs and monthly recurring-service margins in cents."""
from dataclasses import dataclass
import json

import stock

DDL = """
CREATE TABLE IF NOT EXISTS office_stop_costs (
 ticket_id INTEGER PRIMARY KEY REFERENCES office_stops(ticket_id),
 crew_hours INTEGER NOT NULL CHECK(crew_hours>=0),
 hourly_cents INTEGER NOT NULL CHECK(hourly_cents>=0),
 miles INTEGER NOT NULL CHECK(miles>=0),
 mileage_cents INTEGER NOT NULL CHECK(mileage_cents>=0),
 disposal_cents INTEGER NOT NULL CHECK(disposal_cents>=0),
 other_cents INTEGER NOT NULL CHECK(other_cents>=0),
 revision INTEGER NOT NULL CHECK(revision>0)
);
"""

# Hours and miles are in hundredths; rates and expenses are in cents.
FIELDS = (
    ("crew_hours", "Total crew hours", 1_000_000),
    ("hourly_cents", "Labor cost per crew hour ($)", 10_000_000),
    ("miles", "Miles allocated to this stop", 10_000_000),
    ("mileage_cents", "Vehicle cost per mile ($)", 100_000),
    ("disposal_cents", "Disposal fees ($)", 100_000_000),
    ("other_cents", "Other direct cost ($)", 100_000_000),
)
RATES = ("hourly_cents", "mileage_cents")


def read_value(data, name, label, maximum):
    value = data.get(name, "").strip()
    if name == "other_cents" and not value:
        return 0
    return stock.number(value, 100, maximum, label)


def rounded_ratio(value, divisor):
    return (value + divisor // 2) // divisor


def cost_parts(values):
    return (
        rounded_ratio(values["crew_hours"] * values["hourly_cents"], 100),
        rounded_ratio(values["miles"] * values["mileage_cents"], 100),
        values["disposal_cents"],
        values["other_cents"],
    )


def get_costs(con, ticket_id):
    return con.execute("SELECT * FROM office_stop_costs WHERE ticket_id=?", (ticket_id,)).fetchone()


def defaults(con):
    return {r["name"]: r["value"] for r in con.execute(
        "SELECT name,value FROM office_preferences WHERE name IN ('hourly_cents','mileage_cents')")}


def save_defaults(con, data):
    for name, label, maximum in FIELDS:
        if name in RATES:
            value = read_value(data, name, label, maximum)
            con.execute("""INSERT INTO office_preferences(name,value) VALUES (?,?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""", (name, str(value)))


def save_stop_costs(con, ticket_id, data, now):
    previous = get_costs(con, ticket_id)
    values = {name: read_value(data, name, label, maximum) for name, label, maximum in FIELDS}
    if previous and all(values[name] == previous[name] for name in values):
        return
    revision = previous["revision"] if previous else 0
    if data.get("revision", "0") != str(revision):
        raise ValueError("Costs changed since this job opened. Reload and review them before saving.")
    con.execute("""INSERT INTO office_stop_costs
        (ticket_id,crew_hours,hourly_cents,miles,mileage_cents,disposal_cents,other_cents,revision)
        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET
        crew_hours=excluded.crew_hours,hourly_cents=excluded.hourly_cents,miles=excluded.miles,
        mileage_cents=excluded.mileage_cents,disposal_cents=excluded.disposal_cents,
        other_cents=excluded.other_cents,revision=excluded.revision""",
        (ticket_id, *values.values(), revision + 1))
    detail = {"previous": {name: previous[name] for name in values} if previous else None, "actual": values}
    con.execute("INSERT INTO events(ticket_id,timestamp,event,detail) VALUES (?,?,'route_costs_recorded',?)",
                (ticket_id, now, json.dumps(detail)))


def amend_stop_costs(con, ticket_id, data, now):
    stop = stock.lookup(con, "SELECT state FROM office_stops WHERE ticket_id=?", (ticket_id,), "Route stop")
    if stop["state"] == "scheduled":
        raise ValueError("Complete or skip this stop before editing its actual costs.")
    save_stop_costs(con, ticket_id, data, now)


@dataclass
class Totals:
    gross: int = 0
    approved_gross: int = 0
    parent: int = 0
    labor: int = 0
    vehicle: int = 0
    disposal: int = 0
    other: int = 0
    open_stops: int = 0
    missing_costs: int = 0

    @property
    def costs(self):
        return self.labor + self.vehicle + self.disposal + self.other

    @property
    def margin(self):
        return self.gross - self.parent - self.costs


def report(con, first, last):
    """Group by route, account, and route/account, with one split per invoice."""
    month = first[:7]
    plans = con.execute("""SELECT p.id,p.account_id,p.route,a.name,i.ticket_id,
        (t.finalized_at IS NOT NULL) AS approved,
        CASE WHEN i.ticket_id IS NULL THEN p.monthly_cents ELSE l.cents END AS gross
        FROM office_plans p JOIN office_accounts a ON a.id=p.account_id
        LEFT JOIN office_invoices i ON i.account_id=p.account_id AND i.month=?
        LEFT JOIN tickets t ON t.id=i.ticket_id
        LEFT JOIN office_invoice_lines l ON l.ticket_id=i.ticket_id AND l.plan_id=p.id
        WHERE l.plan_id IS NOT NULL OR
            (i.ticket_id IS NULL AND p.start_on<=? AND (p.end_on='' OR p.end_on>=?))
        ORDER BY p.account_id,p.id""", (month, last, first)).fetchall()
    by_route, by_account, by_route_account = {}, {}, {}

    def groups(route, account_id, name):
        return (
            by_route.setdefault(route.casefold(), (route, Totals()))[1],
            by_account.setdefault(account_id, (name, Totals()))[1],
            by_route_account.setdefault((route.casefold(), account_id), (route, name, Totals()))[2],
        )

    # Allocate each invoice's rounded 15% split across its lines without
    # rounding each line separately. The allocated amounts sum to the invoice split.
    running_gross = {}
    for plan in plans:
        account_id = plan["account_id"]
        gross = plan["gross"]
        prior = running_gross.get(account_id, 0)
        running_gross[account_id] = prior + gross
        parent = rounded_ratio((prior + gross) * 15, 100) - rounded_ratio(prior * 15, 100)
        for group in groups(plan["route"], account_id, plan["name"]):
            group.gross += gross
            group.approved_gross += gross if plan["approved"] else 0
            group.parent += parent

    stops = con.execute("""SELECT s.state,p.route,p.account_id,a.name,c.*
        FROM office_stops s JOIN office_plans p ON p.id=s.plan_id
        JOIN office_accounts a ON a.id=p.account_id
        LEFT JOIN office_stop_costs c ON c.ticket_id=s.ticket_id
        WHERE s.day BETWEEN ? AND ?
        ORDER BY s.day,s.ticket_id""", (first, last))
    for stop in stops:
        if stop["state"] == "skipped" and stop["ticket_id"] is None:
            continue
        labor, vehicle, disposal, other = cost_parts(stop) if stop["ticket_id"] is not None else (0, 0, 0, 0)
        for group in groups(stop["route"], stop["account_id"], stop["name"]):
            group.labor += labor
            group.vehicle += vehicle
            group.disposal += disposal
            group.other += other
            group.open_stops += stop["state"] == "scheduled"
            group.missing_costs += stop["state"] == "done" and stop["ticket_id"] is None
    return by_route, by_account, by_route_account
