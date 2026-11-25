"""Regression checks for the merged Aistudio Compose configuration."""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]

SYNTHETIC_ENV = {
    "AISTUDIO_PORT": "7860",
    "AISTUDIO_API_KEY": "synthetic-api-key",
    "AISTUDIO_WEB_PASSWORD": "synthetic-console-password",
    "ANTIGRAVITY_API_KEY": "synthetic-antigravity-api-key",
    "ANTIGRAVITY_WEB_PASSWORD": "synthetic-antigravity-web-password",
    "WORKBUDDY_API_KEY": "synthetic-workbuddy-api-key",
    "POSTGRES_PASSWORD": "synthetic-postgres-password",
    "REDIS_PASSWORD": "synthetic-redis-password",
    "SUB2API_PASSWORD": "synthetic-sub2api-password",
    "JWT_SECRET": "synthetic-jwt-secret",
    "TOTP_ENCRYPTION_KEY": "synthetic-totp-key",
}


def docker_compose_available():
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class AistudioComposeTests(unittest.TestCase):
    def test_proxy_kit_entrypoints_exist(self):
        for name in ("preload.cjs", "novnc.cjs"):
            with self.subTest(name=name):
                self.assertTrue(
                    (ROOT / "services" / "aistudio-novnc" / name).is_file(),
                    "missing services/aistudio-novnc/" + name,
                )

    def test_merged_config_keeps_aistudio_contract(self):
        if not docker_compose_available():
            self.skipTest("docker compose is unavailable")

        with tempfile.TemporaryDirectory() as folder:
            env_file = pathlib.Path(folder) / "aistudio-compose.env"
            env_file.write_text(
                "".join(f"{key}={value}\n" for key, value in SYNTHETIC_ENV.items()),
                encoding="utf-8",
            )
            process = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "--profile",
                    "aistudio",
                    "-f",
                    "compose.yml",
                    "-f",
                    "compose.aistudio-novnc.yml",
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                env=self.compose_environment(),
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )

        self.assertEqual(
            process.returncode,
            0,
            f"docker compose config exited with status {process.returncode}",
        )
        config = json.loads(process.stdout)
        services = config.get("services")
        self.assertIsInstance(services, dict, "merged config has no services")
        self.assertIn("aistudio", services, "merged config has no aistudio service")
        aistudio = services["aistudio"]

        ports = [
            port
            for port in aistudio.get("ports") or []
            if isinstance(port, dict) and str(port.get("target")) == "7860"
        ]
        self.assertEqual(len(ports), 1, "aistudio must expose exactly its 7860 target")
        self.assertEqual(ports[0].get("host_ip"), "0.0.0.0")
        self.assertEqual(str(ports[0].get("published")), "7860")

        self.assertEqual(
            aistudio.get("command"),
            ["node", "--require", "/app/proxy-kit/preload.cjs", "main.js"],
        )

        volumes = [
            volume
            for volume in aistudio.get("volumes") or []
            if isinstance(volume, dict)
        ]
        proxy_mounts = [
            volume for volume in volumes if volume.get("target") == "/app/proxy-kit"
        ]
        self.assertEqual(len(proxy_mounts), 1, "missing /app/proxy-kit mount")
        proxy_mount = proxy_mounts[0]
        self.assertEqual(proxy_mount.get("type"), "bind")
        self.assertIs(proxy_mount.get("read_only"), True)
        self.assertTrue(proxy_mount.get("source"), "/app/proxy-kit mount has no source")
        self.assertEqual(
            pathlib.Path(proxy_mount["source"]).resolve(),
            (ROOT / "services" / "aistudio-novnc").resolve(),
        )

        auth_mounts = [
            volume for volume in volumes if volume.get("target") == "/app/configs/auth"
        ]
        self.assertEqual(len(auth_mounts), 1, "missing aistudio auth mount")

    @staticmethod
    def compose_environment():
        environment = os.environ.copy()
        environment.pop("COMPOSE_FILE", None)
        environment.pop("COMPOSE_PROFILES", None)
        environment.pop("COMPOSE_ENV_FILES", None)
        environment.update(SYNTHETIC_ENV)
        return environment


if __name__ == "__main__":
    unittest.main()
