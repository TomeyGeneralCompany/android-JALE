"""Verify the office workflow through the same HTTP routes the phone uses."""
from contextlib import closing
from datetime import date
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import desk
import office_automation as office


class OfficeWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for target, name, value in (
            (desk, "DB", root / "desk.db"),
            (desk.invoice_pdf, "ROOT", root / "invoices"),
            (office, "today", lambda: self.current),
        ):
            change = patch.object(target, name, value)
            change.start()
            self.addCleanup(change.stop)
        self.current = date(2027, 2, 1)
        desk.init()
        server = desk.ThreadingHTTPServer(("127.0.0.1", 0), desk.Handler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"

    def get(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return response.read().decode()

    def post(self, path, *, origin=True, **data):
        headers = {"Origin": self.base} if origin else {}
        request = Request(self.base + path, urlencode(data).encode(), headers)
        with urlopen(request, timeout=5) as response:
            return response.read().decode()

    def rows(self, sql):
        with closing(desk.db()) as con:
            return [tuple(row) for row in con.execute(sql)]

    def plan(self, **overrides):
        data = dict(account_id="1", request=token_hex(16), site="123 Linwood", route="Copemish",
                    service="Trash pickup", scope="Trash bags from garage entrance", cadence="weekly",
                    start_on="2027-02-01", clock="08:00", monthly="300.00")
        data.update(overrides)
        return self.post("/office/plan", **data)

    def test_sales_import_call_queue_and_intake_match(self):
        with self.assertRaises(HTTPError) as no_origin:
            self.post("/office/seed", origin=False)
        self.assertEqual(no_origin.exception.code, 403)
        self.post("/office/seed")
        self.post("/office/seed")
        count = self.rows("SELECT count(*) FROM office_accounts")[0][0]
        self.assertGreaterEqual(count, 45)
        self.assertEqual(self.rows("SELECT COUNT(DISTINCT account_key) FROM office_accounts"), [(count,)])
        self.post("/office/account", name="Example Manager", area="Kaleva", owner="Sales", phone="(231) 555-0187")
        account_id = self.rows("SELECT id FROM office_accounts WHERE name='Example Manager'")[0][0]
        self.assertIn("Example Manager", self.get("/office?owner=Sales"))
        page = self.post("/office/call", account_id=account_id, stage="contacted", notes="Quoted next week\nUse the west gate.")
        self.assertIn("Follow-up draft to review and copy", page)
        self.assertEqual(self.rows(f"SELECT follow_up FROM office_accounts WHERE id={account_id}"), [("2027-02-04",)])
        self.post("/ticket/new", customer="Example job", phone="+1-231-555-0187", problem="Clean garage")
        self.assertEqual(self.rows("SELECT account_id FROM office_intake"), [(account_id,)])
        self.assertIn("Missing details", self.get("/office"))
        with self.assertRaises(HTTPError):
            self.post("/office/import", csv="name,phone\nNew contact,2315550188\nBad row,123,extra")
        self.assertEqual(self.rows("SELECT count(*) FROM office_accounts WHERE name='New contact'"), [(0,)])

    def test_agreement_dispatch_forecast_and_single_monthly_invoice(self):
        self.post("/office/account", name="County Rentals", area="Copemish")
        token = token_hex(16)
        self.plan(request=token)
        self.plan(request=token)
        self.plan(site="456 Snow Lane", service="Snow removal", cadence="snow", trigger="3.5",
                  end_on="2027-02-28", monthly="700.00")
        self.post("/office/snow", route="copemish", inches="3.49")
        self.assertEqual(self.rows("SELECT count(*) FROM office_stops"), [(2,)])
        self.post("/office/snow", route="Copemish", inches="3.50")
        self.post("/office/snow", route="Copemish", inches="3.50")
        self.assertEqual(self.rows("SELECT count(*) FROM office_stops"), [(3,)])
        ticket_id = self.rows("SELECT ticket_id FROM office_stops ORDER BY ticket_id")[0][0]
        self.assertIn("123 Linwood", self.get("/office/routes.csv"))
        self.assertIn("County Rentals", self.get("/?calendar=2027-02"))
        with self.assertRaises(HTTPError) as premature:
            self.post(f"/ticket/{ticket_id}/finalize", final_total="300")
        self.assertEqual(premature.exception.code, 400)
        self.post("/office/stop", ticket_id=ticket_id, state="done", crew_hours="1", hourly_cents="20",
                  miles="5", mileage_cents="0.70", disposal_cents="0")
        with self.assertRaises(HTTPError) as invoice:
            self.get(f"/ticket/{ticket_id}/invoice.pdf")
        self.assertEqual(invoice.exception.code, 404)
        self.assertIn("$1000.00", self.get("/office?month=2027-02"))
        self.assertIn("$300.00", self.get("/office?month=2027-03"))
        self.current = date(2027, 3, 1)
        desk.run_office_automation()
        desk.run_office_automation()
        self.assertEqual(self.rows("SELECT month,count(*) FROM office_invoices GROUP BY month"), [("2027-02", 1)])
        self.assertEqual(self.rows("SELECT sum(cents),count(*) FROM office_invoice_lines"), [(100_000, 2)])
        invoice_id = self.rows("SELECT ticket_id FROM office_invoices")[0][0]
        self.post("/office/approve-invoice", ticket_id=invoice_id)
        self.post("/office/approve-invoice", ticket_id=invoice_id)
        self.assertEqual(self.rows(f"SELECT bill_total FROM tickets WHERE id={invoice_id}"), [("1000.00",)])
        self.assertEqual(self.rows(f"SELECT count(*) FROM events WHERE ticket_id={invoice_id} AND event='bill_finalized'"), [(1,)])
        with urlopen(self.base + f"/ticket/{invoice_id}/invoice.pdf", timeout=5) as response:
            self.assertTrue(response.read().startswith(b"%PDF"))
        self.post(f"/ticket/{invoice_id}/pay-in-full")
        self.assertEqual(self.rows(f"SELECT applied FROM customer_payments WHERE ticket_id={invoice_id}"), [("1000.00",)])

    def test_month_end_schedule_can_end_without_billing_visit_tickets(self):
        self.current = date(2027, 1, 31)
        self.post("/office/account", name="Cabins", area="Wellston")
        self.plan(start_on="2027-01-31", cadence="monthly")
        self.current = date(2027, 2, 24)
        desk.run_office_automation()
        self.assertEqual(self.rows("SELECT day FROM office_stops ORDER BY day"), [("2027-01-31",), ("2027-02-28",)])
        self.post("/office/end-plan", plan_id="1", end_on="2027-02-25")
        self.assertEqual(self.rows("SELECT state FROM office_stops WHERE day='2027-02-28'"), [("skipped",)])
        self.current = date(2027, 3, 30)
        desk.run_office_automation()
        self.assertEqual(self.rows("SELECT month FROM office_invoices ORDER BY month"), [("2027-01",), ("2027-02",)])
        self.assertEqual(self.rows("SELECT count(*) FROM office_stops"), [(2,)])

    def test_cost_draft_preserves_approved_quote(self):
        self.post("/ticket/new", customer="Example", service="Garage cleanout", problem="Three cubic yards")
        with self.assertRaises(HTTPError):
            self.post("/office/estimate", ticket_id="1", labor_hours="2", labor_rate="40", truck="20")
        page = self.post("/office/estimate", ticket_id="1", labor_hours="2", labor_rate="40", truck="20", disposal="70")
        self.assertIn("Minimum after 15% split: $200.00", page)
        self.assertEqual(self.rows("SELECT status,bill_total,extra_charge FROM tickets"), [("new", "", "0")])

    def test_route_margin_costs_rates_rounding_and_historical_stops(self):
        self.post("/office/account", name="County Rentals", area="Copemish")
        self.plan(monthly="300.03")
        self.plan(monthly="200.03", route="Wellston", site="12 Ranch Rd")
        self.post("/office/route-rates", hourly_cents="30.00", mileage_cents="0.80", margin_month="2027-02")
        self.assertIn('name="mileage_cents" type="number" value="0.80"', self.get("/ticket/1"))
        with self.assertRaises(HTTPError) as missing:
            self.post("/office/stop", ticket_id="1", state="done")
        self.assertEqual(missing.exception.code, 400)
        self.assertEqual(self.rows("SELECT state FROM office_stops WHERE ticket_id=1"), [("scheduled",)])

        actual = dict(crew_hours="1.50", hourly_cents="30", miles="12.50", mileage_cents="0.80",
                      disposal_cents="20.00", other_cents="5.00")
        self.post("/office/stop", ticket_id="1", state="done", **actual)
        self.post("/office/stop", ticket_id="1", state="done", **actual)
        self.post("/office/route-rates", hourly_cents="35", mileage_cents="0.90")
        self.assertIn("Recorded direct cost: $80.00", self.get("/ticket/1"))
        with closing(desk.db()) as con, con:
            # Simulate a completed stop captured before JALE tracked direct costs.
            con.execute("UPDATE office_stops SET state='done' WHERE ticket_id=3")
            by_route, by_account, combined = office.office_margins.report(con, "2027-02-01", "2027-02-28")
        gross = by_account[1][1]
        self.assertEqual((gross.gross, gross.parent, gross.costs, gross.margin), (50_006, 7501, 8000, 34_505))
        self.assertEqual(gross.missing_costs, 1)
        self.assertEqual(by_route["copemish"][1].costs, 8000)
        self.assertEqual(combined[("wellston", 1)][2].missing_costs, 1)

        actual["disposal_cents"] = "25.00"
        with self.assertRaises(HTTPError) as stale:
            self.post("/office/stop-costs", ticket_id="1", revision="0", **actual)
        self.assertEqual(stale.exception.code, 400)
        self.post("/office/stop-costs", ticket_id="1", revision="1", **actual)
        self.post("/office/stop-costs", ticket_id="1", revision="1", **actual)
        self.assertEqual(self.rows("SELECT revision FROM office_stop_costs WHERE ticket_id=1"), [(2,)])
        self.assertEqual(self.rows("SELECT count(*) FROM events WHERE event='route_costs_recorded'"), [(2,)])
        page = self.get("/office?margin_month=2027-02&margin_view=account")
        self.assertIn("Completed without costs", page)
        self.assertIn("$340.05", page)
        self.current = date(2027, 3, 1)
        desk.run_office_automation()
        invoice_id = self.rows("SELECT ticket_id FROM office_invoices")[0][0]
        self.post("/office/approve-invoice", ticket_id=invoice_id)
        with closing(desk.db()) as con:
            _, by_account, _ = office.office_margins.report(con, "2027-02-01", "2027-02-28")
        self.assertEqual(by_account[1][1].approved_gross, 50_006)
