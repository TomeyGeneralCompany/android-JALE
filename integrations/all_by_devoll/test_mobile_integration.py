"""Regression coverage for the mobile Gmail and offline diagnostics paths."""
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import desk
import office_settings
import offline_diagnostics


class MobileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_before = desk.DB
        desk.DB = Path(self.tmp.name) / "jobs.sqlite3"
        desk.init()
        self.home_before = office_settings.HOME_CONFIG
        office_settings.HOME_CONFIG = Path(self.tmp.name) / ".config" / "el-grande" / "desk.env"

    def tearDown(self):
        office_settings.HOME_CONFIG = self.home_before
        desk.DB = self.db_before
        self.tmp.cleanup()

    def test_saved_gmail_settings_restore_after_process_restart(self):
        with patch.dict(os.environ, {}, clear=True):
            office_settings.save({"ABD_IMAP_USER": "tickets@example.com",
                                  "ABD_IMAP_APP_PASSWORD": "abcd efgh ijkl mnop"})
            self.assertEqual(os.environ["ABD_IMAP_APP_PASSWORD"], "abcdefghijklmnop")
            os.environ.pop("ABD_IMAP_USER")
            os.environ.pop("ABD_IMAP_APP_PASSWORD")
            restored = office_settings.load_environment()
            self.assertEqual(restored["ABD_IMAP_USER"], "tickets@example.com")
            self.assertEqual(os.environ["ABD_IMAP_APP_PASSWORD"], "abcdefghijklmnop")

    def test_gmail_ticket_import_and_uid_deduplication(self):
        message = EmailMessage()
        message["Subject"] = "New submission from the website"
        message["Message-ID"] = "<mobile-ticket-test@example.com>"
        message.set_content("Customer: Jane Mobile\nPhone: 231-555-0100\nService: Brake service\nProblem: Squealing brakes\n")

        class FakeIMAP:
            def __init__(self, *args, **kwargs): pass
            def login(self, user, secret):
                assert user == "tickets@example.com" and secret == "abcdefghijklmnop"
            def select(self, mailbox, readonly=False): return ("OK", [b"1"])
            def response(self, name): return ("UIDVALIDITY", [b"123"])
            def uid(self, command, *args):
                if command == "search": return ("OK", [b"42"])
                if command == "fetch": return ("OK", [(b"1 (RFC822 {0})", message.as_bytes())])
                raise AssertionError(command)
            def logout(self): pass

        with patch.dict(os.environ, {"ABD_IMAP_USER":"tickets@example.com",
                                     "ABD_IMAP_APP_PASSWORD":"abcd efgh ijkl mnop"}), \
             patch.object(desk.imaplib, "IMAP4_SSL", FakeIMAP):
            self.assertEqual(desk.import_mailbox_once(), 1)
            self.assertEqual(desk.import_mailbox_once(), 0)
            with desk.db() as con:
                rows = con.execute("SELECT customer,problem FROM tickets").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["customer"], "Jane Mobile")

    def test_android_offline_draft_without_ollama(self):
        job_id, _ = desk.create_ticket({
            "customer":"Test Mobile",
            "service":"Brake repair",
            "problem":"Brakes squealing",
            "machine":"2017 truck"}, "manual:mobile-diagnostics")
        with patch.dict(os.environ, {"JALE_ANDROID":"1"}, clear=True):
            desk.suggest(job_id)
            page = desk.render_ticket(job_id)
        self.assertIn("OFFLINE DIAGNOSTIC CHECKLIST", page)
        self.assertIn("Brake components", page)
        self.assertNotIn("Suggested labor range:", page)

    def test_draft_never_makes_up_prices_or_vehicle_fitment(self):
        result=offline_diagnostics.draft({"service":"Scrap removal", "problem":"Remove old fencing"})
        self.assertIn("Loading supplies", result)
        self.assertIn("No prices, diagnosis or labor hours have been inferred.", result)


if __name__ == "__main__":
    unittest.main()
