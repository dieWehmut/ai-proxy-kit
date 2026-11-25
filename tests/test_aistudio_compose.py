"""Compose contract checks for the four public AI Proxy Kit services.

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

PUBLIC_SERVICES = {"antigravity", "workbuddy", "sub2api", "aistudio"}

SYNTHETIC_ENV = {
    "AISTUDIO_API_KEY": "synthetic-aistudio-api-key",
    "AISTUDIO_ADMIN_PASSWORD": "synthetic-aistudio-admin-password",
    "AISTUDIO_PORT": "2048",
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

    def test_public_service_set_is_exactly_the_four_services(self):
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
        }
        for name, port_number in expected.items():
            with self.subTest(service=name):
                ports = self.service(name).get("ports") or []
                self.assertEqual(len(ports), 1, f"{name} must publish exactly one port")
                port = ports[0]
                self.assertEqual(port.get("host_ip"), "0.0.0.0")
                self.assertEqual(str(port.get("published")), port_number)
                self.assertEqual(str(port.get("target")), port_number)


if __name__ == "__main__":
    unittest.main()
