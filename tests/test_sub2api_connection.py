"""Connector regression checks using synthetic Sub2API and generation responses."""
import contextlib
import importlib.util
import io
import json
import pathlib
import stat
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sub2api_connection", ROOT / "scripts/connect-sub2api.py")
connection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connection)


class FakeHTTP:
    def __init__(self, values):
        self.values = values
        self.group = {"id": 4, "name": "AI Proxy Kit / Aistudio", "platform": "openai"}
        self.account = {
            "id": 3, "name": self.group["name"], "platform": "openai", "type": "apikey",
            "credentials": {"base_url": "http://aistudio:7860"},
            "credentials_status": {"has_api_key": True}, "group_ids": [4],
            "status": "active", "schedulable": True,
        }
        self.accounts = [self.account]
        self.key = {"id": 7, "name": "ai-proxy-kit-aistudio", "group_id": 4, "key": "synthetic-scoped-key"}
        self.keys = [self.key]
        self.models = ["cn:hy4flash-free", "gemini-4-hydrogen", "gemini-fixture"]
        self.requests = []
        self.generation_error = None
        self.key_detail_error = None
        self.created_key_redacted = False
        self.marker = "PROXY_KIT_OK"

    def open(self, request, timeout):
        parsed = urllib.parse.urlsplit(request.full_url)
        method = request.get_method()
        body = json.loads(request.data) if request.data is not None else None
        self.requests.append((method, parsed.path, parsed.query, body))
        if parsed.path == "/v1/models":
            if request.get_header("Authorization") != "Bearer " + self.values["AISTUDIO_API_KEY"]:
                raise AssertionError("Model discovery must authenticate to the upstream")
            return io.BytesIO(json.dumps({"object": "list", "data": [
                {"id": model, "object": "model"} for model in self.models]}).encode())
        if parsed.path == "/v1/chat/completions":
            if parsed.netloc != "sub2api.fixture":
                raise AssertionError("Generation must route through Sub2API")
            if request.get_header("Authorization") != "Bearer synthetic-scoped-key":
                raise AssertionError("Generation must use the original provider-scoped key")
            if self.generation_error:
                raise self.generation_error
            if body["stream"]:
                event = {"choices": [{"delta": {"content": self.marker}}]}
                return io.BytesIO(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": self.marker}}]}).encode())
        if request.get_header("Authorization") != "Bearer " + self.values["SUB2API_JWT"]:
            raise AssertionError("Management API must retain authenticated access")
        if method == "GET" and parsed.path == "/api/v1/admin/compliance":
            data = {"required": False}
        elif method == "GET" and parsed.path == "/api/v1/admin/groups/all":
            data = [self.group]
        elif method == "GET" and parsed.path == "/api/v1/admin/accounts":
            data = self.page(self.accounts, parsed.query)
        elif method == "GET" and parsed.path == "/api/v1/admin/accounts/3":
            data = self.account
        elif method == "GET" and parsed.path == "/api/v1/keys":
            data = self.page(self.keys, parsed.query)
        elif method == "POST" and parsed.path == "/api/v1/keys":
            if body != {"name": "ai-proxy-kit-aistudio", "group_id": 4}:
                raise AssertionError("Create only the requested provider-scoped key")
            self.keys.append(self.key)
            data = dict(self.key)
            if self.created_key_redacted:
                data.pop("key")
        elif method == "GET" and parsed.path == "/api/v1/keys/7":
            if self.key_detail_error:
                raise self.key_detail_error
            data = self.key
        else:
            raise AssertionError("Unexpected API operation")
        return io.BytesIO(json.dumps({"code": 0, "message": "success", "data": data}).encode())

    def page(self, entries, query):
        page = int(urllib.parse.parse_qs(query)["page"][0])
        start = (page - 1) * 100
        return {"items": entries[start:start + 100], "total": len(entries), "page": page,
                "page_size": 100, "pages": max(1, (len(entries) + 99) // 100)}


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self._stack = contextlib.ExitStack()
        self.addCleanup(self._stack.close)
        folder = self._stack.enter_context(tempfile.TemporaryDirectory())
        self.root = pathlib.Path(folder)
        self.values = {"SUB2API_JWT": "synthetic-admin-jwt", "SUB2API_BASE_URL": "http://sub2api.fixture",
                       "AISTUDIO_API_KEY": "synthetic-upstream-key", "AISTUDIO_PORT": "7860"}
        self.http = FakeHTTP(self.values)
        self.output = io.StringIO()
        self.receipts_path = self.root / "runtime/sub2api-connections.json"
        self._stack.enter_context(mock.patch.object(connection.proxykit, "ROOT", self.root))
        self._stack.enter_context(mock.patch.object(connection.proxykit, "read_env", return_value=self.values))
        self._stack.enter_context(mock.patch.object(connection.proxykit, "HTTP", self.http))
        self._stack.enter_context(mock.patch.dict(connection.os.environ, {}, clear=True))
        self._stack.enter_context(mock.patch("sys.stdout", self.output))

    def run_connection(self, *args):
        with mock.patch("sys.argv", ["connect-sub2api.py", "--provider", "aistudio", *args]):
            connection.main()

    def receipt(self):
        return json.loads(self.receipts_path.read_text())["aistudio"]

    def save_success(self, **overrides):
        receipt = {"base_url": "http://sub2api.fixture", "account_id": 3, "group_id": 4,
                   "upstream_url": "http://aistudio:7860", "api_key_id": 7,
                   "api_key": "synthetic-scoped-key", "tested_model": "gemini-4-hydrogen",
                   "generation": "PASS", "stream": False, "operator_note": "retain this metadata"}
        receipt.update(overrides)
        connection.proxykit.private_write(self.receipts_path, json.dumps({
            "aistudio": receipt, "workbuddy": {"account_id": 9, "operator_note": "retain other provider"}}))
        return receipt

    def calls(self, method, path):
        return [request for request in self.http.requests if request[:2] == (method, path)]

    def test_configuration_reruns_preserve_receipt_and_healthy_account(self):
        expected = self.save_success()
        self.run_connection()
        self.run_connection()
        self.assertEqual(self.receipt(), expected)
        self.assertFalse([request for request in self.http.requests if request[0] != "GET"])
        self.assertEqual(json.loads(self.receipts_path.read_text())["workbuddy"]["account_id"], 9)
        self.assertEqual(stat.S_IMODE(self.receipts_path.stat().st_mode), 0o600)

    def test_redacted_account_credentials_are_reused(self):
        for value in (None, "", "***", "prefix****tail", "[REDACTED]", "<redacted>"):
            with self.subTest(mask=value):
                self.http.account["credentials"]["api_key"] = value
                self.run_connection()
        self.assertFalse([request for request in self.http.requests if request[0] != "GET"])

    def test_exposed_credential_mismatch_preserves_existing_receipt(self):
        self.save_success()
        previous = self.receipts_path.read_bytes()
        self.http.account["credentials"]["api_key"] = "synthetic-different-key"
        with self.assertRaisesRegex(ValueError, "different credentials") as failure:
            self.run_connection("--test")
        self.assertNotIn("synthetic-different-key", str(failure.exception))
        self.assertEqual(self.receipts_path.read_bytes(), previous)
        self.assertFalse([request for request in self.http.requests if request[0] != "GET"])

    def test_missing_key_status_does_not_accept_an_exposed_empty_key(self):
        self.http.account["credentials"]["api_key"] = ""
        self.http.account["credentials_status"] = {"has_api_key": False}
        with self.assertRaisesRegex(ValueError, "different credentials"):
            self.run_connection()

    def test_account_identity_and_group_checks_remain_required(self):
        cases = (("platform", "anthropic"), ("type", "oauth"), ("group_ids", []),
                 ("credentials", {"base_url": "http://different.fixture:7860"}))
        for field, value in cases:
            with self.subTest(field=field), mock.patch.dict(self.http.account, {field: value}):
                with self.assertRaises(ValueError):
                    self.run_connection("--test")
        self.assertFalse(self.calls("POST", "/api/v1/keys"))

    def test_hydrogen_is_selected_from_aistudio_models_and_generation_is_routed(self):
        self.run_connection("--test")
        self.assertEqual(self.calls("POST", "/v1/chat/completions")[0][3]["model"], "gemini-4-hydrogen")
        self.assertEqual(self.receipt()["generation"], "PASS")
        self.assertFalse(self.calls("POST", "/api/v1/keys"))
        self.assertNotIn("synthetic-", self.output.getvalue())

    def test_explicit_model_skips_discovery(self):
        self.http.models = []
        self.run_connection("--test", "--model", "gemini-explicit-fixture")
        self.assertFalse(self.calls("GET", "/v1/models"))
        self.assertEqual(self.calls("POST", "/v1/chat/completions")[0][3]["model"], "gemini-explicit-fixture")

    def test_available_model_fallback_does_not_apply_workbuddy_preferences(self):
        self.http.models = ["gemini-fixture", "cn:hy4flash-free"]
        self.run_connection("--test")
        self.assertEqual(self.receipt()["tested_model"], "gemini-fixture")

    def test_real_sse_response_requires_successful_verification(self):
        self.run_connection("--test", "--stream")
        self.assertTrue(self.calls("POST", "/v1/chat/completions")[0][3]["stream"])
        self.assertEqual(self.receipt()["generation"], "PASS")
        self.assertTrue(self.receipt()["stream"])

    def test_models_and_wrong_generation_content_cannot_pass(self):
        self.http.marker = "not the requested marker"
        with self.assertRaisesRegex(ValueError, "verification marker"):
            self.run_connection("--test")
        self.assertEqual(self.receipt()["generation"], "FAIL")
        self.assertNotIn("real generation PASS", self.output.getvalue())

    def test_failed_generation_preserves_new_key_for_retry(self):
        self.http.keys = []
        self.http.generation_error = urllib.error.HTTPError("http://sub2api.fixture/v1/chat/completions",
                                                          503, "Unavailable", {}, io.BytesIO(b"{}"))
        for attempt in range(2):
            with self.subTest(attempt=attempt), self.assertRaises(urllib.error.HTTPError):
                self.run_connection("--test")
            self.assertEqual(self.receipt()["api_key_id"], 7)
            self.assertEqual(self.receipt()["api_key"], "synthetic-scoped-key")
            self.assertEqual(self.receipt()["generation"], "FAIL")
        self.assertEqual(len(self.calls("POST", "/api/v1/keys")), 1)
        self.assertNotIn("real generation PASS", self.output.getvalue())

    def test_failed_generation_retains_previous_success_evidence(self):
        self.save_success()
        self.http.generation_error = TimeoutError("Synthetic generation timeout")
        with self.assertRaises(TimeoutError):
            self.run_connection("--test", "--model", "gemini-retry-fixture", "--stream")
        receipt = self.receipt()
        self.assertEqual(receipt["generation"], "FAIL")
        self.assertEqual(receipt["tested_model"], "gemini-retry-fixture")
        self.assertEqual(receipt["last_success"], {
            "tested_model": "gemini-4-hydrogen", "generation": "PASS", "stream": False})
        self.assertEqual(receipt["api_key_id"], 7)
        self.assertEqual(receipt["operator_note"], "retain this metadata")

    def test_empty_models_preserve_key_and_fail_without_generation(self):
        self.http.models = []
        with self.assertRaisesRegex(ValueError, "no available models"):
            self.run_connection("--test")
        self.assertEqual(self.receipt()["api_key_id"], 7)
        self.assertEqual(self.receipt()["generation"], "FAIL")
        self.assertFalse(self.calls("POST", "/v1/chat/completions"))

    def test_created_key_identity_is_saved_before_detail_failure(self):
        self.http.keys = []
        self.http.created_key_redacted = True
        self.http.key["key"] = "***"
        self.http.key_detail_error = TimeoutError("Synthetic key detail timeout")
        for attempt in range(2):
            with self.subTest(attempt=attempt), self.assertRaises(TimeoutError):
                self.run_connection("--test")
            self.assertEqual(self.receipt()["api_key_id"], 7)
        self.assertEqual(len(self.calls("POST", "/api/v1/keys")), 1)
        self.assertFalse(self.calls("POST", "/v1/chat/completions"))

    def test_redacted_scoped_key_uses_matching_private_receipt(self):
        self.save_success()
        self.http.key["key"] = "***"
        self.run_connection("--test")
        self.assertEqual(self.receipt()["api_key"], "synthetic-scoped-key")
        self.assertEqual(self.receipt()["generation"], "PASS")
        self.assertFalse(self.calls("POST", "/api/v1/keys"))

    def test_redacted_scoped_key_without_receipt_cannot_generate_or_be_recreated(self):
        self.http.key["key"] = "***"
        with self.assertRaisesRegex(ValueError, "scoped API key is redacted"):
            self.run_connection("--test")
        self.assertEqual(self.receipt()["api_key_id"], 7)
        self.assertEqual(self.receipt()["generation"], "FAIL")
        self.assertNotIn("api_key", self.receipt())
        self.assertFalse(self.calls("POST", "/api/v1/keys"))
        self.assertFalse(self.calls("POST", "/v1/chat/completions"))

    def test_accounts_and_scoped_keys_are_reused_after_pagination(self):
        self.http.accounts = [{"id": 100 + number, "name": "other account"} for number in range(100)] + [self.http.account]
        self.http.keys = [{"id": 200 + number, "name": "other key", "group_id": 5} for number in range(100)] + [self.http.key]
        self.run_connection("--test")
        self.assertEqual(len(self.calls("GET", "/api/v1/admin/accounts")), 2)
        self.assertEqual(len(self.calls("GET", "/api/v1/keys")), 2)
        self.assertFalse(self.calls("POST", "/api/v1/keys"))

    def test_receipt_key_is_preferred_when_scoped_names_are_duplicated(self):
        self.save_success()
        self.http.keys.insert(0, dict(self.http.key, id=8, key="synthetic-other-scoped-key"))
        self.run_connection("--test")
        self.assertEqual(self.receipt()["api_key_id"], 7)

    def test_receipts_from_different_connections_do_not_supply_cached_credentials(self):
        for overrides in ({"base_url": "http://another.fixture"}, {"account_id": 30}, {"group_id": 40},
                          {"upstream_url": "http://another-upstream.fixture"}):
            with self.subTest(fields=tuple(overrides)):
                self.save_success(**overrides)
                self.run_connection()
                self.assertNotIn("api_key", self.receipt())
                self.assertNotIn("generation", self.receipt())

    def test_trailing_slashes_preserve_receipt_identity(self):
        self.save_success(base_url="http://sub2api.fixture/", upstream_url="http://aistudio:7860/")
        self.http.account["credentials"]["base_url"] += "/"
        self.run_connection("--base-url", "http://sub2api.fixture/", "--upstream-url", "http://aistudio:7860/")
        self.assertEqual(self.receipt()["api_key_id"], 7)
        self.assertEqual(self.receipt()["generation"], "PASS")

    def test_receipt_replace_failure_leaves_previous_complete_file(self):
        self.save_success()
        previous = self.receipts_path.read_bytes()
        with mock.patch.object(pathlib.Path, "replace", side_effect=OSError("Synthetic replace failure")):
            with self.assertRaises(OSError):
                self.run_connection()
        self.assertEqual(self.receipts_path.read_bytes(), previous)
        self.assertEqual(stat.S_IMODE(self.receipts_path.stat().st_mode), 0o600)

    def test_http_rejection_does_not_echo_upstream_credentials(self):
        payload = io.BytesIO(json.dumps({"reason": self.values["AISTUDIO_API_KEY"]}).encode())
        failure = urllib.error.HTTPError("http://sub2api.fixture/api/v1/admin/accounts", 400, "Rejected", {}, payload)
        api = connection.Sub2API("http://sub2api.fixture", self.values)
        with mock.patch.object(self.http, "open", side_effect=failure):
            with self.assertRaisesRegex(ValueError, "HTTP 400") as result:
                api.admin("GET", "accounts")
        self.assertNotIn(self.values["AISTUDIO_API_KEY"], str(result.exception))


if __name__ == "__main__":
    unittest.main()
