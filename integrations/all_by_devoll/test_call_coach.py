import json
import unittest
from unittest.mock import Mock, patch

import call_coach


class CallCoachTests(unittest.TestCase):
    def test_approved_replies_handle_rejection_and_common_sales_questions(self):
        self.assertEqual(call_coach.suggestion("Stop calling, we already have a vendor."),
                         "Understood. Thanks for your time.")
        self.assertIn("backup", call_coach.suggestion("We already use a company"))
        self.assertIn("firm quote", call_coach.suggestion("What would you charge for that?"))
        self.assertIn("lifting", call_coach.suggestion("Do I have to clean out the garage?"))
        self.assertIn("follow up", call_coach.suggestion("Send me a quote"))

    def test_speech_action_coaches_from_latest_utterance_not_old_topics(self):
        engine = Mock()
        engine.audio.return_value = json.dumps({
            "latest": "", "lastFinal": "Who does the loading in the garage?",
            "complete": "What would it cost? Who does the loading in the garage?", "revision": 3,
        })
        with patch.object(call_coach, "native", return_value=engine):
            data = {"session": "aa000000-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "index": "2", "audio": "AAA="}
            result = call_coach.speech_action("audio", data)
            engine.audio.assert_called_once_with(data["session"], 2, "AAA=")
            self.assertIn("lifting", result["suggestion"])
            with self.assertRaises(ValueError):
                call_coach.speech_action("audio", {**data, "audio": "\ninvalid"})
