"""Email content checks with no account database access or external delivery.

Run from backend/: python -m unittest discover -s tests
"""
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


class ReminderEmailTests(unittest.TestCase):
    def setUp(self):
        # Substitute configuration and account storage before loading the module.
        # Never load the local .env file or initialize the real user database.
        auth = ModuleType("app.auth")
        auth._connect = MagicMock(side_effect=AssertionError("Unexpected database access"))
        config = ModuleType("app.config")
        config.settings = SimpleNamespace(
            SMTP_FROM_EMAIL="reminders@example.invalid",
            SMTP_HOST="smtp.example.invalid",
            SMTP_PORT=587,
            SMTP_USE_SSL=False,
            SMTP_USE_TLS=True,
            SMTP_USERNAME="",
            SMTP_PASSWORD="",
            APP_PUBLIC_URL="http://localhost:5173",
        )
        spec = importlib.util.spec_from_file_location(
            "reminders_under_test",
            Path(__file__).resolve().parents[1] / "app" / "reminders.py",
        )
        self.reminders = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"app.auth": auth, "app.config": config}):
            spec.loader.exec_module(self.reminders)

    def test_email_contains_schedule_and_each_food_instruction(self):
        expected_food = {
            "BF": "before food",
            "AF": "after food",
            "WITH": "with food",
            "EMPTY": "on an empty stomach",
            "NONE": "as prescribed",
        }
        for code, description in expected_food.items():
            with self.subTest(food=code), patch.object(self.reminders.smtplib, "SMTP") as smtp:
                server = smtp.return_value
                server.send_message.return_value = {}
                message_id = self.reminders._send_email_reminder({
                    "patient_email": "patient@example.invalid",
                    "medicine_name": "Example medicine",
                    "dose_text": "2 scheduled doses",
                    "slot": "night",
                    "food_text": self.reminders.FOOD_TEXT[code],
                })
                message = server.send_message.call_args.args[0]
                body = message.get_content()
                self.assertIn("Medicine: Example medicine", body)
                self.assertIn("Dose: 2 scheduled doses", body)
                self.assertIn("Time of day: Night", body)
                self.assertIn(f"Food instructions: {description}", body)
                self.assertIn("already taken this dose", body)
                self.assertIn("Manage your reminders: http://localhost:5173", body)
                self.assertNotIn("Sign in to your patient", body)
                self.assertEqual(message["To"], "patient@example.invalid")
                self.assertEqual(message["Message-ID"], message_id)
                server.starttls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
