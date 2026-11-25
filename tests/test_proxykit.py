"""Regression checks for credential preservation and safe command construction."""
import argparse
import hashlib
import importlib.util
import io
import json
import pathlib
import stat
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
