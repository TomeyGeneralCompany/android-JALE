"""Operator screens for lead work, routes, and recurring billing."""
import csv
import io
import os
from datetime import timedelta
from urllib.parse import urlencode

import dashboard_overview
import customer_payments
import stock
import office_automation as office
import shop_ui
from stock_ui import esc, field, form, hidden, list_table, navigation, section

PAGE = 40


def link(path, title):
    return f'<a href="{esc(path)}">{esc(title)}</a>'


def job(ticket_id):
    return link(f"/ticket/{ticket_id}", f"ABD-{ticket_id:06d}")


def account_link(account_id, name):
    return link(f"/office?account={account_id}#account", name)


def select(name, title, options, value=""):
    choices = "".join(f'<option value="{esc(key)}"{" selected" if key == value else ""}>{esc(label)}</option>'
                      for key, label in options)
    return f'<label>{esc(title)}<select name="{name}">{choices}</select></label>'


def account_fields(row):
    return "".join(field(name, label, row[name] if row else "", kind, required=name == "name")
                   for name, label, kind in (
                       ("name", "Customer or prospect", "text"), ("area", "Town or area", "text"),
                       ("owner", "Sales owner", "text"), ("phone", "Phone", "tel"),
                       ("email", "Email", "email"), ("website", "Website or listing", "url"),
                       ("pitch", "Service to pitch", "text")))


def account_panel(con, row, current):
    account_id = row["id"]
    key = hidden("account_id", account_id)
    contacts = []
    if row["phone"]:
        number = office.phone_key(row["phone"])
        contacts.append(link(f"tel:+1{number}", row["phone"]) if number else esc(row["phone"]))
    if row["email"]:
        contacts.append(esc(row["email"]))
    if row["website"]:
        contacts.append(link(row["website"], "Open website or listing"))
    contact = "<p>" + " · ".join(contacts) + "</p>" if contacts else ""
    history = con.execute("SELECT * FROM office_calls WHERE account_id=? ORDER BY id DESC LIMIT 10",
                          (account_id,)).fetchall()
    call_fields = key + select("stage", "Outcome", [(s, s.title()) for s in office.STAGES], row["stage"])
    call_fields += field("follow_up", "Next follow-up (defaults to three days)", kind="date")
    call_fields += '<label>Call notes<textarea name="notes" maxlength="500" required></textarea></label>'
    details = list_table(("Date", "Outcome", "Notes"), [(esc(entry["happened_at"][:10]),
        esc(entry["outcome"].title()), esc(entry["notes"])) for entry in history])
    if history:
        greeting = f"Hi, this is All by Devoll following up with {row['name']}.\n\n"
        recap = greeting + history[0]["notes"] + "\n\nAll by Devoll\n231-655-9489"
        details += '<details><summary>Follow-up draft to review and copy</summary>'
        details += '<label>Draft<textarea readonly>' + esc(recap) + '</textarea></label></details>'
    edit = form("/office/account-edit", key + account_fields(row), "Save contact")
    plans = con.execute("SELECT * FROM office_plans WHERE account_id=? ORDER BY id DESC", (account_id,))
    plan_rows = []
    for plan in plans:
        plan_id = plan["id"]
        end = plan["end_on"]
        end_form = ""
        if not end or end >= current.isoformat():
            end_form = form("/office/end-plan", hidden("plan_id", plan_id) +
                            field("end_on", "Last service day", end, "date", required=True), "Set end date")
        plan_rows.append((esc(plan["site"]), esc(plan["route"]), esc(plan["service"]), esc(plan["cadence"]),
                          esc(plan["start_on"]), esc(end or "Ongoing"), "$" + office.money(plan["monthly_cents"]),
                          esc(plan["scope"]), end_form))
    plan_fields = key + field("site", "Service address", required=True) + field("route", "Route name", required=True)
    plan_fields += select("service", "Service", [(s, s) for s in office.SERVICES])
    plan_fields += select("cadence", "Visit schedule", [(s, s.title()) for s in office.CADENCES])
    plan_fields += field("scope", "Signed scope (include any limits)", required=True)
    plan_fields += field("start_on", "First service day", current.isoformat(), "date", required=True)
    plan_fields += field("end_on", "Last service day (required for snow)", kind="date")
    plan_fields += field("clock", "Appointment time", "08:00", "time", required=True)
    plan_fields += field("monthly", "Agreed monthly price ($)", kind="number", step=".01", required=True)
    plan_fields += field("trigger", "Snowfall trigger in inches (snow only)", kind="number", step=".01")
    return ('<section class="panel" id="account"><h2>' + esc(row["name"]) + '</h2>' + contact +
            form("/office/call", call_fields, "Log call and schedule follow-up") + details +
            '<details><summary>Edit contact</summary>' + edit + '</details></section>' +
            section("Signed service agreements", list_table(("Property", "Route", "Service", "Cadence", "Starts",
                "Ends", "Monthly", "Scope", "Change end date"), plan_rows) +
                '<p>Each service line bills its full agreed monthly price for every month it overlaps. '
                'Enter a separate price and scope for each property or service.</p>' +
                form("/office/plan", plan_fields, "Add signed service", request=True)))


def sales(con, query, current):
    owner = office.text({"owner": query.get("owner", [""])[0]}, "owner", "sales owner", False)
    filter_owner = ' AND owner=? COLLATE NOCASE' if owner else ''
    due = con.execute("""SELECT * FROM office_accounts WHERE follow_up<>'' AND follow_up<=?""" +
        filter_owner + " ORDER BY follow_up,name LIMIT ?", (current.isoformat(), *((owner,) if owner else ()), PAGE))
    due_rows = [(esc(r["follow_up"]), account_link(r["id"], r["name"]), esc(r["area"]),
                 esc(r["phone"]), esc(r["owner"]), esc(r["pitch"])) for r in due]
    owner_filter = '<form method="get" action="/office">' + field("owner", "Sales owner", owner) + shop_ui.button("Filter call queue") + "</form>"
    queue = section("Calls due", owner_filter + list_table(("Due", "Lead", "Area", "Phone", "Owner", "Pitch"), due_rows))
    before = query.get("before", [""])[0]
    cursor = stock.identifier(before) if before else 2**63 - 1
    rows = con.execute("SELECT * FROM office_accounts WHERE id<? ORDER BY id DESC LIMIT ?",
                       (cursor, PAGE + 1)).fetchall()
    directory = list_table(("Account", "Area", "Status", "Next call", "Owner"), [
        (account_link(r["id"], r["name"]), esc(r["area"]), esc(r["stage"].title()), esc(r["follow_up"]), esc(r["owner"]))
        for r in rows[:PAGE]])
    directory += navigation("/office", "before", before, rows[PAGE-1]["id"] if len(rows) > PAGE else None)
    directory += '<details><summary>Add a prospect or customer</summary>'
    directory += form("/office/account", account_fields(None) + field("follow_up", "First call", kind="date"),
                      "Add prospect") + '</details>'
    directory += ('<details><summary>Import leads</summary>' +
                  form("/office/seed", "", "Load Manistee County call list") +
                  form("/office/import", '<label>Paste CSV with name,phone,email,area,website,owner,follow_up,pitch'
                       '<textarea name="csv" required></textarea></label>', "Import CSV") + '</details>')
    return queue + section("Customers and prospects", directory)


def intake(con):
    rows = con.execute("""SELECT t.*,a.name linked_name,a.id linked_id FROM office_intake i
        JOIN tickets t ON t.id=i.ticket_id LEFT JOIN office_accounts a ON a.id=i.account_id
        WHERE t.finalized_at IS NULL AND t.status IN ('new','estimating','quoted')
        ORDER BY t.id DESC LIMIT ?""", (PAGE,))
    result = []
    for row in rows:
        missing = [label for key, label in (("customer", "name"), ("phone", "phone"), ("location", "location"),
                   ("problem", "scope")) if not row[key].strip()]
        source = "Gmail" if row["source_id"].startswith(("email:", "imap:")) else "Phone / manual"
        linked = account_link(row["linked_id"], row["linked_name"]) if row["linked_id"] else "Review match"
        result.append((job(row["id"]), esc(row["customer"]), esc(source), linked, esc(", ".join(missing))))
    return section("Intake review", list_table(("Job", "Customer", "Source", "Account", "Missing details"), result))


def routes(con, query, current):
    selected = office.parse_month(query.get("dispatch", [""])[0], current)
    start, finish = office.active_month(selected)
    rows = con.execute("""SELECT s.*,p.route,p.site,p.service,a.name FROM office_stops s
        JOIN office_plans p ON p.id=s.plan_id JOIN office_accounts a ON a.id=p.account_id
        WHERE s.day BETWEEN ? AND ? ORDER BY CASE WHEN s.state='scheduled' THEN 0 ELSE 1 END,
        s.day,p.route COLLATE NOCASE,p.clock,p.site""", (start, finish))
    result = []
    for row in rows:
        actions = ""
        if row["state"] == "scheduled":
            for state, label in (("done", "Complete stop"), ("skipped", "Skip stop")):
                actions += form("/office/stop", hidden("ticket_id", row["ticket_id"]) + hidden("state", state) +
                                hidden("dispatch", start[:7]), label)
        result.append((esc(row["day"]), esc(row["route"]), esc(row["site"]), esc(row["name"]),
                       esc(row["service"]), job(row["ticket_id"]), esc(row["state"]), actions))
    snow_fields = field("route", "Snow route", required=True)
    snow_fields += field("inches", "Confirmed inches on this route", kind="number", required=True, step=".01")
    select_month = '<form method="get" action="/office#routes">' + field("dispatch", "Service month", start[:7], "month")
    select_month += shop_ui.button("Show stops") + "</form>"
    return ('<section class="panel" id="routes"><h2>Route stops</h2>' + select_month +
            '<p>Snow dispatch uses today’s confirmed snowfall for the named route. Each eligible site gets one stop per day.</p>' +
            form("/office/snow", snow_fields, "Confirm snow dispatch") +
            list_table(("Date", "Route", "Property", "Customer", "Service", "Job", "Status", ""), result) + "</section>")


def billing(con, query, current, amounts):
    selected = office.parse_month(query.get("month", [office.DEFAULT_TARGET_MONTH])[0], current)
    following = office.next_month(selected)
    totals = office.forecast(con, selected, amounts, dashboard_overview.local_day)
    committed = totals["recurring"] + totals["booked"] + totals["billed_one_off"]
    remaining = max(0, office.MONTHLY_TARGET - committed)
    following_totals = office.forecast(con, following, amounts, dashboard_overview.local_day)
    statuses = dashboard_overview.pair_rows([
        (f"{selected:%B %Y} signed recurring", "$" + office.money(totals["recurring"])),
        ("Booked one-off estimates", "$" + office.money(totals["booked"])),
        ("Billed one-off work", "$" + office.money(totals["billed_one_off"])),
        ("Recurring invoices approved for this service month", "$" + office.money(totals["billed_contracts"])),
        ("Customer payments received", "$" + office.money(totals["paid"])),
        ("Forecast revenue", "$" + office.money(committed)),
        ("Recurring gap to $3,000", "$" + office.money(max(0, 300_000 - totals["recurring"]))),
        ("Forecast gap to $20,000", "$" + office.money(remaining)),
        ("Tomey General share of signed recurring (15%)", "$" + office.money(totals["split"])),
        (f"{following:%B %Y} signed recurring", "$" + office.money(following_totals["recurring"])),
        (f"{following:%B %Y} booked one-off estimates", "$" + office.money(following_totals["booked"])),
    ])
    switch = '<form method="get" action="/office#billing">' + field("month", "Forecast month", selected.isoformat()[:7], "month")
    switch += shop_ui.button("Show forecast") + "</form>"
    invoices = con.execute("""SELECT i.*,t.finalized_at,t.bill_total,t.extra_charge,a.name,a.email
        FROM office_invoices i JOIN tickets t ON t.id=i.ticket_id JOIN office_accounts a ON a.id=i.account_id
        WHERE t.status!='paid' ORDER BY t.finalized_at IS NOT NULL,i.month DESC,i.ticket_id DESC LIMIT ?""", (PAGE,))
    result = []
    for row in invoices:
        ticket_id = row["ticket_id"]
        action = ""
        if not row["finalized_at"]:
            stage = "Draft"
            total = "$" + esc(row["extra_charge"])
            action = form("/office/approve-invoice", hidden("ticket_id", ticket_id), "Approve invoice")
        else:
            owed = customer_payments.balance(con, ticket_id)["due"]
            stage = "Overdue" if owed > 0 and row["due_on"] < current.isoformat() else "Unpaid"
            total = f"${owed:.2f} due"
            if stage == "Overdue":
                reminder = f"Invoice ABD-{ticket_id:06d} for {row['month']} has a balance of ${owed:.2f}. "
                reminder += f"Payment was due {row['due_on']}. Please call All by Devoll at 231-655-9489."
                action = '<details><summary>Review reminder draft</summary><p>' + esc(row["email"]) + '</p>'
                action += '<textarea readonly>' + esc(reminder) + '</textarea></details>'
        result.append((esc(row["month"]), esc(row["name"]), job(ticket_id), esc(row["due_on"]), stage, total, action))
    expiring = con.execute("""SELECT p.site,p.service,p.end_on,a.name,a.id FROM office_plans p
        JOIN office_accounts a ON a.id=p.account_id WHERE p.end_on BETWEEN ? AND ?
        ORDER BY p.end_on,a.name LIMIT ?""", (current.isoformat(), (current + timedelta(days=45)).isoformat(), PAGE))
    renewals = list_table(("Ends", "Account", "Property", "Service"), [
        (esc(row["end_on"]), account_link(row["id"], row["name"]), esc(row["site"]), esc(row["service"]))
        for row in expiring])
    return ('<section class="panel" id="billing"><h2>Revenue and billing</h2>' + switch + statuses +
            '<h3>Agreements ending in the next 45 days</h3>' + renewals +
            '<h3>Invoice and collections queue</h3>' +
            list_table(("Service month", "Account", "Invoice", "Due", "Status", "Amount", ""), result) + "</section>")


def page(con, query, amounts):
    current = office.today()
    account_id = query.get("account", [""])[0]
    toolbar = '<div class="footactions">' + link("#routes", "Routes") + link("#billing", "Revenue and billing")
    toolbar += " " + link("/office/routes.csv", "Export upcoming routes") + '</div>'
    if os.getenv("JALE_ANDROID") == "1":
        shared = con.execute("SELECT value FROM office_preferences WHERE name='shared_desk'").fetchone()
        url = shared[0] if shared else ""
        if url:
            toolbar += '<p>' + link(url + "/office", "Open shared office desk") + '</p>'
        toolbar += ('<details><summary>Shared office desk</summary>'
                    '<p>Open the same hosted desk on the phone and in the office. '
                    'The phone database remains available for standalone use.</p>' +
                    form("/office/shared", field("url", "Hosted desk HTTPS address", url, "url"),
                         "Save shared desk address") + '</details>')
    content = section("Office automation", toolbar)
    if account_id:
        content += account_panel(con, office.account(con, stock.identifier(account_id)), current)
    return shop_ui.page_shell(content + sales(con, query, current) + intake(con) +
                             routes(con, query, current) + billing(con, query, current, amounts))


def ticket_panel(con, ticket):
    ticket_id = ticket["id"]
    kind = office.protected_kind(con, ticket_id)
    key = hidden("ticket_id", ticket_id)
    if kind == "stop":
        stop = con.execute("SELECT day,state FROM office_stops WHERE ticket_id=?", (ticket_id,)).fetchone()
        controls = ""
        if stop["state"] == "scheduled":
            controls = form("/office/stop", key + hidden("state", "done"), "Complete stop")
            controls += form("/office/stop", key + hidden("state", "skipped"), "Skip stop")
        return section("Contracted stop", '<p>' + esc(stop["day"]) + ' · ' + esc(stop["state"].title()) +
                       '. Covered by the monthly agreement.</p>' + controls), True
    if kind == "invoice":
        lines = con.execute("SELECT * FROM office_invoice_lines WHERE ticket_id=? ORDER BY plan_id", (ticket_id,))
        invoice = con.execute("SELECT month,due_on FROM office_invoices WHERE ticket_id=?", (ticket_id,)).fetchone()
        summary = f'<p>Service month {esc(invoice["month"])} · Due {esc(invoice["due_on"])}</p>'
        if not ticket["finalized_at"]:
            summary += form("/office/approve-invoice", key, "Approve invoice")
        return section("Monthly service invoice", summary + list_table(("Service and property", "Agreed price"),
            [(esc(row["description"]), "$" + office.money(row["cents"])) for row in lines])), not ticket["finalized_at"]
    if ticket["finalized_at"] or ticket["status"] == "cancelled":
        return "", False
    linked = con.execute("""SELECT a.* FROM office_intake i JOIN office_accounts a ON a.id=i.account_id
                             WHERE i.ticket_id=?""", (ticket_id,)).fetchone()
    accounts = con.execute("SELECT id,name,area FROM office_accounts ORDER BY name,area")
    options = [(str(r["id"]), f"{r['name']} · {r['area']}") for r in accounts]
    match_form = form("/office/link", key + select("account_id", "Customer account", [("", "Choose an account")] + options,
                      str(linked["id"]) if linked else ""), "Link account")
    corrections = key + "".join(field(name, label, ticket[name], kind) for name, label, kind in (
        ("customer", "Customer", "text"), ("phone", "Phone", "tel"), ("location", "Service address", "text"),
        ("service", "Service", "text"), ("problem", "Scope", "text")))
    match_form += '<details><summary>Complete intake details</summary>' + form(
        "/office/intake-details", corrections, "Save intake details") + '</details>'
    estimate = key + "".join(field(name, label, kind="number", required=True, step=".01") for name, label in (
        ("labor_hours", "Labor hours"), ("labor_rate", "Cost per labor hour ($)"),
        ("truck", "Truck cost ($)"), ("disposal", "Disposal cost ($)")))
    minimum = ""
    saved = con.execute("""SELECT detail FROM events WHERE ticket_id=? AND event='office_estimate'
                           ORDER BY id DESC LIMIT 1""", (ticket_id,)).fetchone()
    if saved:
        minimum = '<p>' + esc(saved["detail"]) + '</p>'
    return section("Office intake and pricing", '<details><summary>Link account or complete intake</summary>' +
        match_form + '</details><details><summary>Internal cost estimate</summary>' + minimum +
        form("/office/estimate", estimate, "Calculate minimum price") + '</details>'), False


def route_csv(con, current):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("date", "time", "route", "address", "customer", "service", "scope", "job"))
    rows = con.execute("""SELECT s.day,COALESCE(j.appointment_time,p.clock) AS clock,
        p.route,p.site,a.name,p.service,p.scope,s.ticket_id FROM office_stops s
        JOIN office_plans p ON p.id=s.plan_id JOIN office_accounts a ON a.id=p.account_id
        LEFT JOIN job_appointments j ON j.ticket_id=s.ticket_id
        WHERE s.state='scheduled' AND s.day BETWEEN ? AND ?
        ORDER BY s.day,p.route,clock,p.site""",
        (current.isoformat(), (current + timedelta(days=office.VISIT_HORIZON - 1)).isoformat()))
    for row in rows:
        safe = [("'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v) for v in tuple(row)[:-1]]
        writer.writerow(safe + [f"ABD-{row['ticket_id']:06d}"])
    return output.getvalue()
