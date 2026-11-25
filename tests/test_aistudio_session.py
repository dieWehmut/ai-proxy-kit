"""Google session import checks; all cookies below are synthetic fixtures."""
import importlib.util
import contextlib
import io
import json
import pathlib
import stat
import subprocess
import sys
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

    def test_required_cookie_fields_identify_the_cookie_and_field(self):
        for field in ("name", "value", "domain", "path", "httpOnly", "secure", "sameSite"):
            with self.subTest(field=field):
                cookie = self.cookie()
                del cookie[field]
                with self.assertRaisesRegex(ValueError, f"Cookie #2 is missing required field '{field}'"):
                    session.normalize({"cookies": [self.cookie(), cookie]}, now=100)

    def test_invalid_cookie_values_fail_with_safe_diagnostics(self):
        for field, value, error in (
                ("domain", None, "invalid domain"),
                ("value", 123, "string name, value, domain and path"),
                ("httpOnly", "true", "boolean httpOnly and secure"),
                ("secure", 1, "boolean httpOnly and secure"),
                ("sameSite", [], "invalid sameSite"),
                ("expires", True, "invalid expiration"),
                ("expires", "200", "invalid expiration"),
                ("expires", float("nan"), "invalid expiration"),
                ("expires", float("inf"), "invalid expiration")):
            with self.subTest(field=field, error=error):
                cookie = dict(self.cookie(), **{field: value})
                with self.assertRaisesRegex(ValueError, error):
                    session.normalize({"cookies": [cookie]}, now=100)

    def test_empty_or_expired_sign_in_is_rejected_even_with_other_google_cookies(self):
        other = dict(self.cookie(), name="NID", expires=-1)
        for sign_in in (dict(self.cookie(), value=""), dict(self.cookie(), expires=100)):
            with self.assertRaisesRegex(ValueError, "no unexpired Google sign-in cookie.*SID"):
                session.normalize({"cookies": [sign_in, other]}, now=100)

    def test_secure_google_sign_in_and_optional_expiration_are_supported(self):
        for name in ("__Secure-1PSID", "__Secure-3PSID"):
            with self.subTest(name=name):
                cookie = dict(self.cookie(), name=name)
                del cookie["expires"]
                result = session.normalize({"cookies": [cookie]}, now=300)
                self.assertEqual(result["cookies"][0]["expires"], -1)

    def test_malformed_storage_state_has_precise_errors(self):
        for data in ([], {"cookies": None}, {"cookies": {}}):
            with self.assertRaisesRegex(ValueError, "cookies array"):
                session.normalize(data)
        with self.assertRaisesRegex(ValueError, "Cookie #1 must be an object"):
            session.normalize({"cookies": [None]})

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

    def test_failed_publication_cleans_pending_file_and_preserves_existing_account(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            original = root / "auth-0.json"
            original.write_text('{"accountName":"existing fixture account"}')
            state = session.normalize({"cookies": [self.cookie()]}, now=100)
            with mock.patch.object(session.os, "link", side_effect=PermissionError("fixture publication failure")):
                with self.assertRaises(PermissionError):
                    session.save_auth(state, root)
            self.assertEqual(list(root.iterdir()), [original])
            self.assertEqual(json.loads(original.read_text()), {"accountName": "existing fixture account"})
            self.assertNotIn("proxyKitSourceDigest", state)


class ReloadAuthTests(unittest.TestCase):
    def values(self):
        return {"AISTUDIO_PORT": "7860", "AISTUDIO_WEB_PASSWORD": "synthetic-console-password"}

    def payload(self, **updates):
        status = dict(browserConnected=True, currentAuthIndex=0, isSystemBusy=False,
                      accountDetails=[dict(index=0, isExpired=False, isInvalid=False, name="private account label")])
        status.update(updates)
        return {"status": status, "logs": "private status logs"}

    def response(self, url, body):
        response = io.BytesIO(body)
        response.geturl = mock.Mock(return_value=url)
        return response

    def test_reload_targets_explicit_console_and_returns_only_readiness_fields(self):
        base = "http://127.0.0.1:17860/studio"
        opener = mock.Mock()
        opener.open.side_effect = [self.response(base + "/", b"console"),
                                   self.response(base + "/api/status", json.dumps(self.payload()).encode())]
        values = self.values()
        del values["AISTUDIO_PORT"]
        with mock.patch.object(session.urllib.request, "build_opener", return_value=opener) as build:
            result = session.reload_auth(values, base + "/", auth_index=0)
        self.assertTrue(result["ready"])
        self.assertEqual(result["importedAccount"], "recognized")
        self.assertNotIn("logs", result)
        self.assertNotIn("accountDetails", result)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1].cookiejar, session.http.cookiejar.CookieJar)
        login, status = opener.open.call_args_list
        self.assertEqual(login.args[0], base + "/login")
        self.assertEqual(session.urllib.parse.parse_qs(login.args[1].decode()),
                         {"password": [values["AISTUDIO_WEB_PASSWORD"]]})
        self.assertEqual(status.args, (base + "/api/status",))

    def test_default_console_uses_configured_local_port(self):
        self.assertEqual(session.console_url(self.values()), "http://127.0.0.1:7860")

    def test_configuration_and_url_errors_happen_before_network_access(self):
        cases = [({}, None, "AISTUDIO_WEB_PASSWORD"),
                 ({"AISTUDIO_WEB_PASSWORD": ""}, "http://127.0.0.1:7860", "AISTUDIO_WEB_PASSWORD"),
                 ({"AISTUDIO_WEB_PASSWORD": "synthetic-console-password"}, None, "AISTUDIO_PORT")]
        cases.extend((self.values(), base, "base URL") for base in (
            "", "file:///tmp/console", "http:///console", "http://127.0.0.1:bad", "http://127.0.0.1:0",
            "http://127.0.0.1:65536", "http://user:private@127.0.0.1:7860", "http://127.0.0.1:7860?private=1",
            "http://127.0.0.1:7860/#private", "http://127.0.0.1:7860/ spaced"))
        for values, base, error in cases:
            with self.subTest(error=error), mock.patch.object(session.urllib.request, "build_opener") as build:
                with self.assertRaisesRegex(ValueError, error):
                    session.reload_auth(values, base)
                build.assert_not_called()

    def test_login_failure_with_error_query_is_detected(self):
        opener = mock.Mock()
        opener.open.return_value = self.response("http://127.0.0.1:7860/login?error=1", b"login")
        with mock.patch.object(session.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "console login failed"):
                session.reload_auth(self.values())
        self.assertEqual(opener.open.call_count, 1)

    def test_status_redirect_to_login_is_a_session_failure(self):
        opener = mock.Mock()
        opener.open.side_effect = [self.response("http://127.0.0.1:7860/", b"console"),
                                   self.response("http://127.0.0.1:7860/login?error=1", b"login")]
        with mock.patch.object(session.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "console session expired"):
                session.reload_auth(self.values())

    def test_non_json_status_fails_without_echoing_the_response_body(self):
        opener = mock.Mock()
        opener.open.side_effect = [self.response("http://127.0.0.1:7860/", b"console"),
                                   self.response("http://127.0.0.1:7860/api/status", b"private response body")]
        with mock.patch.object(session.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "status response must be valid JSON") as raised:
                session.reload_auth(self.values())
        self.assertNotIn("private response body", str(raised.exception))

    def test_disconnected_busy_missing_and_expired_accounts_are_not_ready(self):
        cases = [({"browserConnected": False}, "recognized"),
                 ({"isSystemBusy": True}, "recognized"),
                 ({"currentAuthIndex": -1}, "recognized"),
                 ({"accountDetails": []}, "missing"),
                 ({"accountDetails": [dict(index=0, isExpired=True)]}, "expired"),
                 ({"accountDetails": [dict(index=0, isInvalid=True)]}, "invalid")]
        for updates, imported in cases:
            with self.subTest(imported=imported, fields=list(updates)):
                result = session.status_summary(self.payload(**updates), auth_index=0)
                self.assertFalse(result["ready"])
                self.assertEqual(result["importedAccount"], imported)
        result = session.status_summary(self.payload(accountDetails=[dict(index=1)]), auth_index=0)
        self.assertFalse(result["ready"])
        self.assertEqual(result["importedAccount"], "missing")

    def test_actual_readiness_checks_active_account_even_without_import_index(self):
        result = session.status_summary(self.payload(accountDetails=[dict(index=0, isExpired=True)]))
        self.assertFalse(result["ready"])
        self.assertEqual(result["expiredAccounts"], 1)
        self.assertFalse(session.status_summary(self.payload(accountDetails=[]))["ready"])
        self.assertTrue(session.status_summary(self.payload())["ready"])

    def test_other_healthy_account_does_not_make_the_import_ready(self):
        result = session.status_summary(self.payload(
            currentAuthIndex=1,
            accountDetails=[dict(index=0, isExpired=False, isInvalid=False),
                            dict(index=1, isExpired=False, isInvalid=False)]), auth_index=0)
        self.assertTrue(result["browserConnected"])
        self.assertEqual(result["importedAccount"], "recognized")
        self.assertEqual(result["currentAuthIndex"], 1)
        self.assertFalse(result["ready"])

    def test_reload_polls_until_the_imported_index_becomes_ready(self):
        base = "http://127.0.0.1:7860"
        opener = mock.Mock()
        opener.open.side_effect = [
            self.response(base + "/", b"console"),
            self.response(base + "/api/status", json.dumps(self.payload(browserConnected=False)).encode()),
            self.response(base + "/api/status", json.dumps(self.payload()).encode()),
        ]
        with mock.patch.object(session.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(session.time, "monotonic", side_effect=[0.0, 0.4]), \
                mock.patch.object(session.time, "sleep") as sleep:
            result = session.reload_auth(self.values(), base, auth_index=0, ready_timeout=5)
        self.assertTrue(result["ready"])
        self.assertEqual(opener.open.call_count, 3)
        sleep.assert_called_once_with(1.0)

    def test_reload_times_out_and_explains_the_last_status(self):
        base = "http://127.0.0.1:7860"
        other_ready = self.payload(
            currentAuthIndex=1,
            accountDetails=[dict(index=0, isExpired=False, isInvalid=False),
                            dict(index=1, isExpired=False, isInvalid=False)])
        opener = mock.Mock()
        opener.open.side_effect = [
            self.response(base + "/", b"console"),
            self.response(base + "/api/status", json.dumps(other_ready).encode()),
            self.response(base + "/api/status", json.dumps(other_ready).encode()),
        ]
        with mock.patch.object(session.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(session.time, "monotonic", side_effect=[0.0, 0.0, 2.0]), \
                mock.patch.object(session.time, "sleep") as sleep:
            with self.assertRaises(session.ReadinessTimeout) as raised:
                session.reload_auth(self.values(), base, auth_index=0, ready_timeout=2)
        self.assertIn("within 2 seconds", str(raised.exception))
        self.assertIn("currentAuthIndex=1", str(raised.exception))
        self.assertIn("browserConnected=true", str(raised.exception))
        self.assertNotIn("private status logs", str(raised.exception))
        self.assertEqual(opener.open.call_count, 3)
        sleep.assert_called_once_with(1.0)

    def test_expired_or_invalid_import_stops_polling_immediately(self):
        base = "http://127.0.0.1:7860"
        for field, imported in (("isExpired", "expired"), ("isInvalid", "invalid")):
            with self.subTest(field=field):
                opener = mock.Mock()
                opener.open.side_effect = [
                    self.response(base + "/", b"console"),
                    self.response(base + "/api/status", json.dumps(self.payload(
                        accountDetails=[dict(index=0, **{field: True})])).encode()),
                ]
                with mock.patch.object(session.urllib.request, "build_opener", return_value=opener), \
                        mock.patch.object(session.time, "monotonic", return_value=0), \
                        mock.patch.object(session.time, "sleep") as sleep:
                    result = session.reload_auth(self.values(), base, auth_index=0, ready_timeout=60)
                self.assertFalse(result["ready"])
                self.assertEqual(result["importedAccount"], imported)
                self.assertEqual(opener.open.call_count, 2)
                sleep.assert_not_called()

    def test_malformed_status_is_rejected_instead_of_coercing_truthy_flags(self):
        cases = [([], "must be an object"),
                 ({"status": None}, "boolean browserConnected"),
                 ({"status": {}}, "boolean browserConnected"),
                 (self.payload(browserConnected="false"), "boolean browserConnected"),
                 (self.payload(currentAuthIndex=True), "currentAuthIndex or isSystemBusy"),
                 (self.payload(isSystemBusy="false"), "currentAuthIndex or isSystemBusy"),
                 (self.payload(accountDetails={}), "accountDetails array"),
                 (self.payload(accountDetails=[None]), "accountDetails fields"),
                 (self.payload(accountDetails=[dict(index=0, isExpired="false")]), "accountDetails fields")]
        for data, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                session.status_summary(data)


class SessionCLITests(unittest.TestCase):
    def export(self, path):
        cookie = dict(SessionImportTests().cookie(), expires=-1)
        session.proxykit.private_write(path, json.dumps({"cookies": [cookie]}))

    def run_main(self, root, source, *flags):
        output = io.StringIO()
        with mock.patch.object(session.proxykit, "ROOT", root), \
                mock.patch.object(sys, "argv", [str(spec.origin), str(source), *map(str, flags)]), \
                contextlib.redirect_stdout(output):
            result = session.main()
        return result, output.getvalue()

    def test_custom_deployment_flags_write_only_to_the_explicit_auth_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            source, env, auth = root / "session.json", root / "deployment/stack.env", root / "deployment/auth"
            self.export(source)
            session.proxykit.private_write(env, "AISTUDIO_WEB_PASSWORD=synthetic-console-password\n")
            summary = session.status_summary(ReloadAuthTests().payload(), auth_index=0)
            base = "http://127.0.0.1:17860/studio"
            with mock.patch.object(session, "reload_auth", return_value=summary) as reload:
                result, output = self.run_main(root, source, "--env-file", env, "--auth-dir", auth, "--base-url", base)
            self.assertEqual(result, 0)
            self.assertEqual(list(auth.glob("auth-*.json")), [auth / "auth-0.json"])
            self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((auth / "auth-0.json").stat().st_mode), 0o600)
            self.assertFalse((root / "runtime/aistudio/auth").exists())
            self.assertEqual(reload.call_args.args[1:], (base, 0))
            self.assertEqual(reload.call_args.kwargs, {"ready_timeout": 60})
            self.assertIn("ready=true", output)
            self.assertNotIn("synthetic-console-password", output)
            self.assertNotIn("private status logs", output)

    def test_ready_timeout_returns_one_with_the_last_safe_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            source, env = root / "session.json", root / "runtime/stack.env"
            self.export(source)
            session.proxykit.private_write(env, "AISTUDIO_PORT=7860\nAISTUDIO_WEB_PASSWORD=synthetic-console-password\n")
            summary = session.status_summary(ReloadAuthTests().payload(
                currentAuthIndex=1,
                accountDetails=[dict(index=0, isExpired=False, isInvalid=False),
                                dict(index=1, isExpired=False, isInvalid=False)]), auth_index=0)
            errors = io.StringIO()
            with mock.patch.object(session, "reload_auth", side_effect=session.ReadinessTimeout(3, summary)) as reload, \
                    contextlib.redirect_stderr(errors):
                result, output = self.run_main(root, source, "--ready-timeout", 3)
            self.assertEqual(result, 1)
            self.assertEqual(reload.call_args.kwargs, {"ready_timeout": 3})
            self.assertIn("within 3 seconds", errors.getvalue())
            self.assertIn("currentAuthIndex=1", errors.getvalue())
            self.assertNotIn("synthetic-console-password", output + errors.getvalue())

    def test_expired_import_returns_one_with_a_specific_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            source, env = root / "session.json", root / "runtime/stack.env"
            self.export(source)
            session.proxykit.private_write(env, "AISTUDIO_PORT=7860\nAISTUDIO_WEB_PASSWORD=synthetic-console-password\n")
            summary = session.status_summary(ReloadAuthTests().payload(
                accountDetails=[dict(index=0, isExpired=True)]), auth_index=0)
            with mock.patch.object(session, "reload_auth", return_value=summary):
                result, output = self.run_main(root, source, "--ready-timeout", 60)
            self.assertEqual(result, 1)
            self.assertIn("auth-0.json is expired", output)
            self.assertNotIn("synthetic-console-password", output)

    def test_default_paths_and_retry_preserve_import_when_browser_is_disconnected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            source, env = root / "session.json", root / "runtime/stack.env"
            self.export(source)
            session.proxykit.private_write(env, "AISTUDIO_PORT=7860\nAISTUDIO_WEB_PASSWORD=synthetic-console-password\n")
            summary = session.status_summary(ReloadAuthTests().payload(browserConnected=False), auth_index=0)
            with mock.patch.object(session, "reload_auth", return_value=summary):
                first_result, first_output = self.run_main(root, source)
                second_result, second_output = self.run_main(root, source)
            self.assertEqual((first_result, second_result), (1, 1))
            self.assertIn("browserConnected=false", first_output)
            self.assertIn("ready=false", first_output)
            self.assertIn("Reused", second_output)
            self.assertEqual(len(list((root / "runtime/aistudio/auth").glob("auth-*.json"))), 1)
            self.assertNotIn("reloaded the local session", first_output)

    def test_missing_env_and_configuration_fail_before_creating_auth(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            source, env = root / "session.json", root / "deployment.env"
            self.export(source)
            with self.assertRaisesRegex(ValueError, "AI Studio env file is missing"):
                self.run_main(root, source, "--env-file", env)
            session.proxykit.private_write(env, "AISTUDIO_PORT=7860\n")
            with self.assertRaisesRegex(ValueError, "AISTUDIO_WEB_PASSWORD"):
                self.run_main(root, source, "--env-file", env)
            self.assertFalse((root / "runtime/aistudio/auth").exists())

    def test_missing_export_reports_the_wait_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            with mock.patch.object(session.time, "monotonic", side_effect=[0, 1, 2]), \
                    mock.patch.object(session.time, "sleep") as sleep:
                with self.assertRaisesRegex(ValueError, "Session export file is missing.*after waiting 2 seconds"):
                    self.run_main(root, root / "missing.json", "--wait", 2)
            sleep.assert_called_once_with(1)

    def test_export_appearing_during_wait_is_imported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            source, env = root / "session.json", root / "runtime/stack.env"
            session.proxykit.private_write(env, "AISTUDIO_PORT=7860\nAISTUDIO_WEB_PASSWORD=synthetic-console-password\n")
            summary = session.status_summary(ReloadAuthTests().payload(), auth_index=0)
            with mock.patch.object(session.time, "sleep", side_effect=lambda seconds: self.export(source)), \
                    mock.patch.object(session, "reload_auth", return_value=summary):
                result, output = self.run_main(root, source, "--wait", 2)
            self.assertEqual(result, 0)
            self.assertIn("Imported", output)

    def test_negative_wait_is_a_cli_error(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stderr(io.StringIO()):
            root = pathlib.Path(folder)
            with self.assertRaises(SystemExit) as raised:
                self.run_main(root, root / "missing.json", "--wait", -1)
            self.assertEqual(raised.exception.code, 2)

    def test_negative_ready_timeout_is_a_cli_error(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stderr(io.StringIO()):
            root = pathlib.Path(folder)
            with self.assertRaises(SystemExit) as raised:
                self.run_main(root, root / "missing.json", "--ready-timeout", -1)
            self.assertEqual(raised.exception.code, 2)

    def test_invalid_json_and_directory_source_have_precise_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            source = root / "session.json"
            for content in (b"{invalid JSON", b"\xff"):
                source.write_bytes(content)
                with self.assertRaisesRegex(ValueError, "valid UTF-8 browser storage-state JSON"):
                    self.run_main(root, source)
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                self.run_main(root, root)

    def test_script_entry_point_exits_with_precise_required_cookie_error(self):
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / "session.json"
            cookie = dict(SessionImportTests().cookie(), expires=-1)
            del cookie["value"]
            session.proxykit.private_write(source, json.dumps({"cookies": [cookie]}))
            result = subprocess.run([sys.executable, spec.origin, str(source)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Cookie #1 is missing required field 'value'", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
