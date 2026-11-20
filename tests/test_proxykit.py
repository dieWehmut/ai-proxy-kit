"""Regression checks for credential preservation and safe command construction."""
import argparse
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
