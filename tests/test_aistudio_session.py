"""Google session import checks; all cookies below are synthetic fixtures."""
import importlib.util
import json
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("aistudio_session", ROOT / "scripts/import-aistudio-session.py")
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class SessionImportTests(unittest.TestCase):
    def cookie(self):
        return dict(name="SID", value="synthetic-test-session", domain=".google.com", path="/",
                    expires=200, httpOnly=True, secure=True, sameSite="Lax")

    def test_rejects_unrelated_domains_and_expired_sign_in(self):
        for host in ("example.com", "google.com.example.org"):
            cookie = self.cookie()
            cookie["domain"] = host
            with self.assertRaisesRegex(ValueError, "outside google.com"):
                session.normalize({"cookies": [cookie]}, now=100)
        with self.assertRaisesRegex(ValueError, "no unexpired Google sign-in"):
            session.normalize({"cookies": [self.cookie()]}, now=201)

    def test_session_cookie_survives_but_unrelated_storage_is_not_imported(self):
        cookie = self.cookie()
        cookie["expires"] = -1
        result = session.normalize({"cookies": [cookie], "origins": [{"origin": "https://example.com"}]}, now=300)
        self.assertEqual(result["cookies"], [cookie])
        self.assertEqual(result["origins"], [])

    def test_import_is_private_idempotent_and_preserves_existing_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            original = root / "auth-0.json"
            original.write_text('{"accountName":"existing fixture account"}')
            state = session.normalize({"cookies": [self.cookie()]}, now=100)
            first, created = session.save_auth(state, root)
            second, duplicate_created = session.save_auth(state, root)
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(first, second)
            self.assertEqual(first.name, "auth-1.json")
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            self.assertEqual(json.loads(original.read_text()), {"accountName": "existing fixture account"})

    def test_concurrent_import_cannot_overwrite_another_account_or_publish_partial_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            link = session.os.link
            attempts = []

            def import_another_account_first(source, destination):
                self.assertTrue(json.loads(source.read_text())["cookies"])
                if not attempts:
                    destination.write_text('{"accountName":"concurrent fixture account"}')
                attempts.append(destination)
                return link(source, destination)

            state = session.normalize({"cookies": [self.cookie()]}, now=100)
            with mock.patch.object(session.os, "link", side_effect=import_another_account_first):
                file, created = session.save_auth(state, root)
            self.assertTrue(created)
            self.assertEqual(file.name, "auth-1.json")
            self.assertEqual(json.loads((root / "auth-0.json").read_text())["accountName"], "concurrent fixture account")
            self.assertFalse(list(root.glob(".import-*")))


if __name__ == "__main__":
    unittest.main()
