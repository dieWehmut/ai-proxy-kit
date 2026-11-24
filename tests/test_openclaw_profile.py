"""Tests for deterministic, credential-free OpenClaw profile export."""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "openclaw_profile", ROOT / "scripts/export-openclaw-profile.py"
)
profile_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile_export)


class ProfileExportTests(unittest.TestCase):
    def fixture(self):
        return {
            "wizard": {"lastRunAt": "2026-11-24T00:00:00Z", "lastRunVersion": "2026.11.7"},
            "gateway": {"auth": {"mode": "token", "token": "a" * 64}},
            "channels": {"qq": {"accessToken": "b" * 32, "queueDebounceMs": 60000}},
            "models": {
                "providers": {
                    "sub2api": {
                        "apiKey": {"source": "env", "provider": "default", "id": "SUB2API_API_KEY"},
                        "timeoutSeconds": 120,
                    },
                    "legacy": {"apiKey": "sk-live-should-not-survive"},
                }
            },
            "mcp": {"servers": {"example": {"env": {"SERVICE_TOKEN": "c" * 40, "PATH_HINT": "/root/bin"}}}},
            "agents": {"defaults": {"model": {"primary": "sub2api/cn:model"}, "maxTokens": 128000}},
        }

    def test_redacts_credentials_but_preserves_config_shape(self):
        clean = profile_export.sanitize(self.fixture())
        self.assertEqual(clean["gateway"]["auth"]["token"], profile_export.REDACTED)
        self.assertEqual(clean["channels"]["qq"]["accessToken"], profile_export.REDACTED)
        self.assertEqual(clean["models"]["providers"]["legacy"]["apiKey"], profile_export.REDACTED)
        self.assertEqual(
            clean["models"]["providers"]["sub2api"]["apiKey"],
            {"source": "env", "provider": "default", "id": "SUB2API_API_KEY"},
        )
        self.assertEqual(clean["mcp"]["servers"]["example"]["env"]["SERVICE_TOKEN"], profile_export.REDACTED)
        self.assertEqual(clean["mcp"]["servers"]["example"]["env"]["PATH_HINT"], "/root/bin")
        self.assertEqual(clean["channels"]["qq"]["queueDebounceMs"], 60000)
        self.assertEqual(clean["agents"]["defaults"]["maxTokens"], 128000)
        self.assertNotIn("lastRunAt", clean["wizard"])
        self.assertEqual(clean["wizard"]["lastRunVersion"], "2026.11.7")

    def test_output_is_deterministic_and_has_no_obvious_leak(self):
        first = json.dumps(profile_export.sanitize(self.fixture()), sort_keys=True)
        second = json.dumps(profile_export.sanitize(self.fixture()), sort_keys=True)
        self.assertEqual(first, second)
        self.assertNotIn("a" * 64, first)
        self.assertNotIn("b" * 32, first)
        self.assertNotIn("c" * 40, first)
        profile_export.validate_no_obvious_leaks(json.loads(first))

    def test_validation_rejects_credential_shaped_string(self):
        with self.assertRaisesRegex(ValueError, "credential-shaped"):
            profile_export.validate_no_obvious_leaks({"unexpected": "d" * 48})

    def test_rejects_non_object_configuration(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            profile_export.sanitize([])


if __name__ == "__main__":
    unittest.main()
