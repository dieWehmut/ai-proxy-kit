"""Regression checks for credential preservation and safe command construction."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("proxykit", ROOT / "lib/proxykit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)
build_spec = importlib.util.spec_from_file_location("workbuddy_build", ROOT / "tools/build-workbuddy-proxy.py")
workbuddy_build = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(workbuddy_build)


class ConfigurationTests(unittest.TestCase):
    def args(self, root):
        return argparse.Namespace(env_file=root / "runtime/stack.env", import_antigravity=None,
                                  antigravity_data=None, import_workbuddy=None, upstream_proxy=None,
                                  **{name + "_port": None for name in (*kit.SERVICES, "aistudio")})

    def test_reinstall_retains_keys_accounts_and_edited_workbuddy_settings(self):
        with tempfile.TemporaryDirectory(prefix="proxy kit ") as folder, mock.patch.object(kit, "ROOT", pathlib.Path(folder)):
            root = pathlib.Path(folder)
            args = self.args(root)
            first = kit.initialize(args)
            workbuddy = root / "runtime/workbuddy/config.json"
            config = json.loads(workbuddy.read_text())
            config["unknown_user_option"] = {"keep": True}
            workbuddy.write_text(json.dumps(config))
            account = root / "runtime/workbuddy/auths/own.json"
            account.write_text('{"token":"fixture only"}')
            second = kit.initialize(args)
            self.assertEqual(first, second)
            self.assertEqual(len({second[k] for k in kit.SECRET_KEYS}), len(kit.SECRET_KEYS))
            self.assertEqual(stat.S_IMODE(args.env_file.stat().st_mode), 0o600)
            self.assertEqual(account.read_text(), '{"token":"fixture only"}')
            self.assertEqual(json.loads(workbuddy.read_text())["unknown_user_option"], {"keep": True})

    def test_existing_antigravity_credentials_are_imported_once(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(kit, "ROOT", pathlib.Path(folder)):
            root = pathlib.Path(folder)
            old = root / "old.env"
            old.write_text("API_KEY=old-api\nWEB_PASSWORD=old-admin\n")
            args = self.args(root)
            args.import_antigravity = old
            first = kit.initialize(args)
            old.write_text("API_KEY=different-api\nWEB_PASSWORD=different-admin\n")
            second = kit.initialize(args)
            self.assertEqual(first["ANTIGRAVITY_API_KEY"], "old-api")
            self.assertEqual(second["ANTIGRAVITY_API_KEY"], "old-api")
            self.assertEqual(second["ANTIGRAVITY_WEB_PASSWORD"], "old-admin")

    def test_duplicate_ports_fail_before_persisting_configuration(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(kit, "ROOT", pathlib.Path(folder)):
            args = self.args(pathlib.Path(folder))
            args.sub2api_port = 8045
            with self.assertRaisesRegex(ValueError, "ports must be distinct"):
                kit.initialize(args)
            self.assertFalse(args.env_file.exists())

    def test_aistudio_is_part_of_all_and_defaults_do_not_require_its_port_argument(self):
        self.assertIn("aistudio", kit.SERVICES)
        self.assertEqual(len(kit.SERVICES), len(set(kit.SERVICES)))
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(kit, "ROOT", pathlib.Path(folder)):
            args = self.args(pathlib.Path(folder))
            delattr(args, "aistudio_port")
            values = kit.initialize(args)
            self.assertEqual(values["AISTUDIO_PORT"], kit.DEFAULTS["AISTUDIO_PORT"])
            self.assertEqual(values["AISTUDIO_IMAGE"], kit.DEFAULTS["AISTUDIO_IMAGE"])
            self.assertTrue(values["AISTUDIO_API_KEY"])
            self.assertTrue(values["AISTUDIO_WEB_PASSWORD"])

    def test_start_all_targets_aistudio(self):
        with tempfile.TemporaryDirectory() as folder:
            env = pathlib.Path(folder) / "stack.env"
            values = {name.upper() + "_PORT": str(18045 + index)
                      for index, name in enumerate(kit.SERVICES)}
            values.update({key: "fixture" for key in kit.SECRET_KEYS})
            kit.save_env(env, values)
            with mock.patch.object(kit, "docker_command", return_value=["docker"]), \
                    mock.patch.object(kit, "compose") as compose, \
                    mock.patch.object(kit, "ensure_images") as images, \
                    mock.patch.object(kit, "wait_service"), \
                    mock.patch.object(kit, "enable_antigravity"), \
                    mock.patch.object(kit, "write_local_access"), \
                    mock.patch.object(kit, "service_url", return_value="http://127.0.0.1"), \
                    mock.patch.object(sys, "argv", ["proxykit.py", "start", "all", "--env-file", str(env)]):
                kit.main()
            self.assertEqual(images.call_args.args[3], list(kit.SERVICES))
            up = [call.args for call in compose.call_args_list if "up" in call.args]
            self.assertEqual(up[0][-len(kit.SERVICES):], tuple(kit.SERVICES))

    def test_aistudio2api_overlay_is_only_used_for_its_explicit_up(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "compose.yml").touch()
            (root / "compose.aistudio-novnc.yml").touch()
            (root / "compose.aistudio2api.yml").touch()
            models = root / "services/aistudio2api/models.json"
            models.parent.mkdir(parents=True)
            models.touch()
            args = self.args(root)
            with mock.patch.object(kit, "ROOT", root), mock.patch.object(kit, "run") as run:
                kit.compose(["docker"], args, {}, "up", "-d", "aistudio2api")
                explicit = run.call_args.args[0]
                kit.compose(["docker"], args, {}, "up", "-d", "all")
                all_services = run.call_args.args[0]
                models.unlink()
                kit.compose(["docker"], args, {}, "up", "-d", "aistudio2api")
                without_models = run.call_args.args[0]
            self.assertIn(str(root / "compose.aistudio2api.yml"), explicit)
            self.assertNotIn(str(root / "compose.aistudio2api.yml"), all_services)
            self.assertNotIn(str(root / "compose.aistudio2api.yml"), without_models)

    def test_env_roundtrip_is_data_and_never_shell_code(self):
        with tempfile.TemporaryDirectory(prefix="space ") as folder:
            path = pathlib.Path(folder) / "settings.env"
            fixture = {"TOKEN": 'literal$()`command` # text "quote"', "PATH_VALUE": folder}
            kit.save_env(path, fixture)
            self.assertEqual(kit.read_env(path), fixture)
            path.write_text("TOKEN=a\nTOKEN=b\n")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                kit.read_env(path)

class LocalAccessTests(unittest.TestCase):
    def args(self, root):
        return argparse.Namespace(env_file=root / "runtime/stack.env", import_antigravity=None,
                                  antigravity_data=None, import_workbuddy=None, upstream_proxy=None,
                                  **{name + "_port": None for name in (*kit.SERVICES, "aistudio")})

    def test_access_hints_follow_stack_env_without_leaking_into_git(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(kit, "ROOT", pathlib.Path(folder)):
            root = pathlib.Path(folder)
            values = kit.initialize(self.args(root))
            values["AISTUDIO_PORT"] = "17860"
            values["AISTUDIO_WEB_PASSWORD"] = "rotated-console-password"
            kit.write_local_access(values)
            hints = (root / "runtime/LOCAL_ACCESS.txt").read_text()
            console = (root / "runtime/open-aistudio.html").read_text()
            self.assertIn(values["AISTUDIO_API_KEY"], hints)
            self.assertIn("rotated-console-password", hints)
            self.assertIn("http://127.0.0.1:17860/login", console)
            self.assertIn("rotated-console-password", console)
            self.assertEqual(stat.S_IMODE((root / "runtime/LOCAL_ACCESS.txt").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((root / "runtime/open-aistudio.html").stat().st_mode), 0o600)
            first = hints
            kit.write_local_access(values)
            self.assertEqual((root / "runtime/LOCAL_ACCESS.txt").read_text(), first)

    def test_workbuddy_build_retains_configuration_edited_while_checking_binary(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(kit, "ROOT", pathlib.Path(folder)), \
                mock.patch.object(workbuddy_build, "proxykit", kit):
            root = pathlib.Path(folder)
            env_file = root / "runtime/stack.env"
            kit.save_env(env_file, {"AISTUDIO_IMAGE": "fixture:old", "WORKBUDDY_ENV_PROXY_PATCH": "false"})
            binary = root / "runtime/workbuddy/wb2api-proxy"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"fixture binary")
            sha256 = hashlib.sha256
            binary.with_suffix(".json").write_text(json.dumps({
                "commit": workbuddy_build.COMMIT, "binary_sha256": sha256(binary.read_bytes()).hexdigest()}))

            def change_configuration_during_verification(data):
                kit.save_env(env_file, {"AISTUDIO_IMAGE": "fixture:updated", "WORKBUDDY_ENV_PROXY_PATCH": "false"})
                return sha256(data)

            with mock.patch.object(workbuddy_build.hashlib, "sha256", side_effect=change_configuration_during_verification):
                workbuddy_build.build(env_file)
            self.assertEqual(kit.read_env(env_file), {
                "AISTUDIO_IMAGE": "fixture:updated", "WORKBUDDY_ENV_PROXY_PATCH": "true"})


class GenerationChecks(unittest.TestCase):
    def opener(self, payload):
        return mock.Mock(open=mock.Mock(return_value=io.BytesIO(payload)))

    def response(self, url, body=b""):
        response = io.BytesIO(body)
        response.geturl = mock.Mock(return_value=url)
        return response

    def aistudio_values(self):
        return {"AISTUDIO_PORT": "7860", "AISTUDIO_API_KEY": "fixture-key",
                "AISTUDIO_WEB_PASSWORD": "synthetic-console-password"}

    @staticmethod
    def aistudio_status(**updates):
        status = {"currentAuthIndex": 0,
                  "accountDetails": [{"index": 0, "isExpired": False, "isInvalid": False}]}
        status.update(updates)
        return {"status": status, "logs": "private status logs"}

    @contextlib.contextmanager
    def aistudio_check(self, status_body, model="gemini-4-hydrogen", models=None):
        base = "http://127.0.0.1:7860"
        unauthorized = urllib.error.HTTPError(base + "/v1/models", 401, "Unauthorized", {}, None)
        opener = mock.Mock()
        opener.open.side_effect = [
            self.response(base + "/", b"console"),
            self.response(base + "/api/status", json.dumps(status_body).encode()),
        ]
        models = [{"id": model}] if models is None else models
        with mock.patch.object(kit, "wait_service"), \
                mock.patch.object(kit, "request", side_effect=[unauthorized, {"data": models}]) as request, \
                mock.patch.object(kit.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(kit, "verify_generation") as generation:
            yield opener, request, generation

    def test_stream_requires_content_and_termination(self):
        data = b'data: {"choices":[{"delta":{"content":"PROXY_"}}]}\n\ndata: {"choices":[{"delta":{"content":"KIT_OK"}}]}\n\ndata: [DONE]\n'
        with mock.patch.object(kit, "HTTP", self.opener(data)):
            kit.verify_generation("http://local-gateway", "fixture-key", "model", stream=True)
        with mock.patch.object(kit, "HTTP", self.opener(data.replace(b'data: [DONE]\n', b''))):
            with self.assertRaisesRegex(ValueError, "without \\[DONE\\]"):
                kit.verify_generation("http://local-gateway", "fixture-key", "model", stream=True)

    def test_error_or_thinking_only_stream_cannot_pass(self):
        error = b'data: {"error":{"message":"upstream refused"}}\n\ndata: [DONE]\n'
        with mock.patch.object(kit, "HTTP", self.opener(error)):
            with self.assertRaisesRegex(ValueError, "error inside the stream"):
                kit.verify_generation("http://local-gateway", "fixture-key", "model", stream=True)
        empty = b'data: {"choices":[{"delta":{"reasoning_content":"thinking only"}}]}\n\ndata: [DONE]\n'
        with mock.patch.object(kit, "HTTP", self.opener(empty)):
            with self.assertRaisesRegex(ValueError, "verification marker"):
                kit.verify_generation("http://local-gateway", "fixture-key", "model", stream=True)

    def test_sub2api_model_check_cannot_silently_skip_generation(self):
        with mock.patch.object(kit, "wait_service"), mock.patch.object(kit, "verify_generation") as generation:
            with self.assertRaisesRegex(ValueError, "connect.sh"):
                kit.check("sub2api", {}, "gemini-4-hydrogen", stream=True)
            generation.assert_not_called()

    def test_aistudio_requires_the_advertised_model_before_console_login(self):
        unauthorized = urllib.error.HTTPError("http://local-gateway", 401, "Unauthorized", {}, None)
        scenarios = [
            ([], "empty model list"),
            ([{"id": "gemini-3-flash-preview"}], "does not advertise"),
        ]
        for models, message in scenarios:
            with self.subTest(message=message), mock.patch.object(kit, "wait_service"), \
                    mock.patch.object(kit, "request", side_effect=[unauthorized, {"data": models}]), \
                    mock.patch.object(kit.urllib.request, "build_opener") as build, \
                    mock.patch.object(kit, "verify_generation") as generation:
                with self.assertRaisesRegex(ValueError, message):
                    kit.check("aistudio", self.aistudio_values(), "gemini-4-hydrogen")
                build.assert_not_called()
                generation.assert_not_called()

    def test_aistudio_model_check_logs_in_and_uses_the_current_unexpired_account(self):
        values = self.aistudio_values()
        output, errors = io.StringIO(), io.StringIO()
        status = self.aistudio_status(browserConnected=False)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.aistudio_check(status) as (opener, request, generation):
                kit.check("aistudio", values, "gemini-4-hydrogen", stream=True)
        generation.assert_called_once_with("http://127.0.0.1:7860", "fixture-key", "gemini-4-hydrogen", True)
        login, status_request = opener.open.call_args_list
        self.assertEqual(login.args[0], "http://127.0.0.1:7860/login")
        self.assertEqual(urllib.parse.parse_qs(login.args[1].decode()),
                         {"password": [values["AISTUDIO_WEB_PASSWORD"]]})
        self.assertNotIn(values["AISTUDIO_WEB_PASSWORD"], login.args[0])
        self.assertEqual(status_request.args, ("http://127.0.0.1:7860/api/status",))
        self.assertEqual([call.args[0] for call in request.call_args_list],
                         ["http://127.0.0.1:7860/v1/models", "http://127.0.0.1:7860/v1/models"])
        self.assertNotIn(values["AISTUDIO_WEB_PASSWORD"], output.getvalue() + errors.getvalue())

    def test_aistudio_rejects_invalid_current_accounts_without_leaking_the_password(self):
        cases = [
            (self.aistudio_status(currentAuthIndex=-1), "no current account"),
            (self.aistudio_status(currentAuthIndex=1), "not an available account"),
            (self.aistudio_status(accountDetails=[{"index": 0, "isExpired": True}]), "expired"),
            (self.aistudio_status(accountDetails=[{"index": 0, "isInvalid": True}]), "invalid"),
        ]
        password = self.aistudio_values()["AISTUDIO_WEB_PASSWORD"]
        for status, message in cases:
            with self.subTest(message=message):
                with self.aistudio_check(status) as (_, _, generation):
                    with self.assertRaisesRegex(ValueError, message) as raised:
                        kit.check("aistudio", self.aistudio_values(), "gemini-4-hydrogen")
                self.assertNotIn(password, str(raised.exception))
                generation.assert_not_called()

    def test_aistudio_console_login_failure_is_detected_without_leaking_the_password(self):
        base = "http://127.0.0.1:7860"
        opener = mock.Mock(open=mock.Mock(return_value=self.response(base + "/login?error=1", b"login")))
        unauthorized = urllib.error.HTTPError(base + "/v1/models", 401, "Unauthorized", {}, None)
        with mock.patch.object(kit, "wait_service"), \
                mock.patch.object(kit, "request", side_effect=[unauthorized, {"data": [{"id": "gemini-4-hydrogen"}]}]), \
                mock.patch.object(kit.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(kit, "verify_generation") as generation:
            with self.assertRaisesRegex(ValueError, "console login failed") as raised:
                kit.check("aistudio", self.aistudio_values(), "gemini-4-hydrogen")
        self.assertNotIn(self.aistudio_values()["AISTUDIO_WEB_PASSWORD"], str(raised.exception))
        self.assertEqual(opener.open.call_count, 1)
        generation.assert_not_called()

    def test_aistudio_auth_only_check_does_not_require_a_console_session(self):
        unauthorized = urllib.error.HTTPError("http://local-gateway", 401, "Unauthorized", {}, None)
        with mock.patch.object(kit, "wait_service"), \
                mock.patch.object(kit, "request", side_effect=[unauthorized, {"data": [{"id": "gemini-4-hydrogen"}]}]), \
                mock.patch.object(kit.urllib.request, "build_opener") as build:
            kit.check("aistudio", self.aistudio_values())
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
