"""Compose contract checks for the five public AI Proxy Kit services.

The contract covers the services that publish host ports.  PostgreSQL and
Redis remain private dependencies of sub2api and do not expose ports.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]

PUBLIC_SERVICES = {"antigravity", "workbuddy", "sub2api", "aistudio", "qoder"}

SYNTHETIC_ENV = {
    "AISTUDIO_API_KEY": "synthetic-aistudio-api-key",
    "AISTUDIO_ADMIN_PASSWORD": "synthetic-aistudio-admin-password",
    "AISTUDIO_PORT": "2048",
    "QODER_API_KEY": "synthetic-qoder-api-key",
    "QODER_PANEL_PASSWORD": "synthetic-qoder-panel-password",
    "QODER_PORT": "8790",
    "ANTIGRAVITY_API_KEY": "synthetic-antigravity-api-key",
    "ANTIGRAVITY_WEB_PASSWORD": "synthetic-antigravity-web-password",
    "WORKBUDDY_API_KEY": "synthetic-workbuddy-api-key",
    "POSTGRES_PASSWORD": "synthetic-postgres-password",
    "REDIS_PASSWORD": "synthetic-redis-password",
    "SUB2API_PASSWORD": "synthetic-sub2api-password",
    "JWT_SECRET": "synthetic-jwt-secret",
    "TOTP_ENCRYPTION_KEY": "synthetic-totp-key",
}

# Host overrides must not leak into the resolved configuration.
SCRUBBED_KEYS = set(SYNTHETIC_ENV) | {
    "AISTUDIO_IMAGE",
    "QODER_IMAGE",
    "QODER_HOSTNAME",
    "AISTUDIO_ADMIN_AUTH",
    "AISTUDIO_ADMIN_USER",
    "AISTUDIO_CHANNELS",
    "AISTUDIO_WAA_BACKEND",
    "ANTIGRAVITY_IMAGE",
    "ANTIGRAVITY_PORT",
    "WORKBUDDY_IMAGE",
    "WORKBUDDY_PORT",
    "SUB2API_IMAGE",
    "SUB2API_PORT",
    "SUB2API_EMAIL",
    "UPSTREAM_PROXY",
    "TZ",
    "LOCAL_UID",
    "LOCAL_GID",
    "COMPOSE_FILE",
    "COMPOSE_PROFILES",
    "COMPOSE_ENV_FILES",
    "COMPOSE_PROJECT_NAME",
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


def compose_environment():
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in SCRUBBED_KEYS
    }
    environment.update(SYNTHETIC_ENV)
    return environment


class AistudioComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not docker_compose_available():
            raise unittest.SkipTest("docker compose is unavailable")

        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temp_dir.cleanup)
        env_file = pathlib.Path(cls._temp_dir.name) / "synthetic-compose.env"
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
                "antigravity",
                "--profile",
                "workbuddy",
                "--profile",
                "sub2api",
                "--profile",
                "aistudio",
                "--profile",
                "qoder",
                "-f",
                "compose.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=compose_environment(),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if process.returncode != 0:
            raise AssertionError(
                "docker compose config exited with status "
                f"{process.returncode}: {process.stderr.strip()}"
            )

        cls.config = json.loads(process.stdout)
        cls.services = cls.config.get("services") or {}

    @staticmethod
    def published_service_names(services):
        return {
            name
            for name, service in services.items()
            if isinstance(service, dict) and service.get("ports")
        }

    def service(self, name):
        self.assertIn(name, self.services, f"missing {name} service")
        return self.services[name]

    def test_public_service_set_is_exactly_the_five_services(self):
        self.assertEqual(self.published_service_names(self.services), PUBLIC_SERVICES)
        for name in PUBLIC_SERVICES:
            self.assertIn(name, self.services)
        self.assertNotIn("aistudio2api", self.services)

    def test_aistudio_uses_the_playground_image_and_port(self):
        aistudio = self.service("aistudio")
        self.assertEqual(aistudio.get("image"), "ghcr.io/mag1cfall/aistudio2api:latest")

        ports = aistudio.get("ports") or []
        self.assertEqual(len(ports), 1, "aistudio must publish exactly one port")
        port = ports[0]
        self.assertEqual(port.get("host_ip"), "0.0.0.0")
        self.assertEqual(str(port.get("published")), "2048")
        self.assertEqual(str(port.get("target")), "2048")

    def test_aistudio_environment_matches_the_playground_contract(self):
        environment = self.service("aistudio").get("environment") or {}
        expected = {
            "LISTEN_ADDR": "0.0.0.0:2048",
            "AISTUDIO_AUTH_STATES": "/data/auth",
            "UPSTREAM_CHANNELS": "playground",
            "AUTO_START": "true",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(environment.get(key), value)

        self.assertIn("ADMIN_AUTH_ENABLED", environment)
        self.assertIn(
            str(environment["ADMIN_AUTH_ENABLED"]).lower(),
            {"true", "false"},
            "ADMIN_AUTH_ENABLED must be a boolean value",
        )
        # Values from the temporary env file prove that no runtime stack env
        # file supplied the required credentials.
        self.assertEqual(
            environment.get("PROXY_API_KEY"), SYNTHETIC_ENV["AISTUDIO_API_KEY"]
        )
        self.assertEqual(
            environment.get("ADMIN_PASSWORD"),
            SYNTHETIC_ENV["AISTUDIO_ADMIN_PASSWORD"],
        )

    def test_aistudio_mounts_runtime_data(self):
        volumes = self.service("aistudio").get("volumes") or []
        data_mounts = [
            volume
            for volume in volumes
            if isinstance(volume, dict) and volume.get("target") == "/data"
        ]
        self.assertEqual(len(data_mounts), 1, "aistudio must mount exactly /data")
        mount = data_mounts[0]
        self.assertEqual(mount.get("type"), "bind")
        self.assertEqual(
            pathlib.Path(mount.get("source", "")).resolve(),
            (ROOT / "runtime" / "aistudio" / "data").resolve(),
        )

    def test_other_services_keep_their_public_ports(self):
        expected = {
            "antigravity": "8045",
            "workbuddy": "7863",
            "sub2api": "8080",
            "qoder": "8790",
        }
        for name, port_number in expected.items():
            with self.subTest(service=name):
                ports = self.service(name).get("ports") or []
                self.assertEqual(len(ports), 1, f"{name} must publish exactly one port")
                port = ports[0]
                self.assertEqual(port.get("host_ip"), "0.0.0.0")
                self.assertEqual(str(port.get("published")), port_number)
                self.assertEqual(str(port.get("target")), port_number)

    def test_qoder_pins_the_api_key_and_passes_the_panel_password_as_a_flag(self):
        qoder = self.service("qoder")
        self.assertEqual(qoder.get("image"), "ghcr.io/shuishuipingan/qoder2api-hub:latest")

        environment = qoder.get("environment") or {}
        self.assertEqual(environment.get("API_KEY"), SYNTHETIC_ENV["QODER_API_KEY"])
        self.assertEqual(environment.get("QODER_PANEL_PASSWORD"), SYNTHETIC_ENV["QODER_PANEL_PASSWORD"])
        self.assertEqual(environment.get("HOST"), "0.0.0.0")
        self.assertEqual(str(environment.get("PORT")), "8790")

        # --lan forces an API key; the panel password must reach the CLI as a flag
        # so the app hashes it into settings.json. The source must use `$$`: a
        # single `$` is interpolated on the host and arrives EMPTY (verified with
        # a real compose run). The rendered config must therefore still reference
        # the variable (never the clear-text password).
        command = qoder.get("command") or []
        self.assertIn("sh", command)
        script = command[-1]
        self.assertIn("--lan", script)
        self.assertIn("$$QODER_PANEL_PASSWORD", script, "a single $ would be interpolated on the host and arrive empty")
        # The clear-text password is only ever an environment value (like API_KEY);
        # the command line itself must carry the variable reference, not the value.
        self.assertNotIn(SYNTHETIC_ENV["QODER_PANEL_PASSWORD"], script)

        volumes = qoder.get("volumes") or []
        targets = {v.get("target") for v in volumes if isinstance(v, dict)}
        self.assertEqual(targets, {"/app/accounts", "/app/logs", "/app/usage"})
        accounts = next(v for v in volumes if isinstance(v, dict) and v.get("target") == "/app/accounts")
        self.assertEqual(accounts.get("type"), "bind")
        self.assertEqual(
            pathlib.Path(accounts.get("source", "")).resolve(),
            (ROOT / "runtime" / "qoder" / "accounts").resolve(),
        )
        healthcheck = (qoder.get("healthcheck") or {}).get("test") or []
        self.assertTrue(any("/ping" in part for part in healthcheck), "qoder healthcheck must probe /ping")


if __name__ == "__main__":
    unittest.main()
