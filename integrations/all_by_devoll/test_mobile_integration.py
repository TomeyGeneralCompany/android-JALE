"""Regression coverage for the mobile Gmail and offline diagnostics paths."""
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import desk
import office_settings


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

    def test_android_real_qwen_inference_path(self):
        import sys
        import types
        calls=[]
        class FakeQwen:
            @staticmethod
            def generate(prompt):
                calls.append(prompt)
                return ('{"labor_hours_min":1,"labor_hours_max":2,'
                        '"diagnostic_steps":["Inspect brakes"],'
                        '"parts_to_check":["brake pads"],'
                        '"uncertainties":["vehicle condition"]}')
        mock_java = types.ModuleType("java")
        mock_java.jclass=lambda name: FakeQwen if name == "com.tomeygeneral.elgrande.QwenNative" else None
        job_id, _=desk.create_ticket({
            "customer":"Phone owner","service":"Brake repair",
            "problem":"Brake squeal","machine":"2017 truck"},"manual:qwen-native-test")
        with (patch.dict(os.environ, {"JALE_ANDROID": "1"}, clear=True),
              patch.dict(sys.modules, {"java": mock_java}),
              patch.object(desk.urllib.request, "urlopen",
                           side_effect=AssertionError("Android must not call network model"))):
            desk.suggest(job_id)
        self.assertEqual(len(calls),1)
        self.assertIn("Brake squeal",calls[0])
        self.assertIn("/no_think",calls[0])
        page=desk.render_ticket(job_id)
        self.assertIn("Inspect brakes",page)
        self.assertIn("Suggested labor range",page)
        self.assertNotIn("OFFLINE DIAGNOSTIC CHECKLIST",page)


if __name__ == "__main__":
    unittest.main()
