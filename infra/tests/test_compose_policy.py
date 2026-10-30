"""Policy checks on the rendered production compose file (specs WP-1.5a and WP-1.5b; ARCHITECTURE §2,
DEPLOYMENT, ADR-007, ADR-009, ADR-026 (3), ADR-005, ADR-004, .claude/rules/infra.md). Stdlib only:

    python3 -m unittest discover -s infra/tests -v      (needs the docker compose plugin)
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path(__file__).with_name("ci.env")
SERVICES = {
    "caddy", "web", "api", "migrate", "worker", "scheduler",
    "postgres", "redis", "sandbox-manager", "docker-socket-proxy", "egress-proxy",
}
# ARCHITECTURE §2 "Keanggotaan network (mengikat)".
NETWORKS = {
    "caddy": {"public", "edge"},
    "web": {"edge"},
    "api": {"edge", "app"},
    "worker": {"app", "outbound"},
    "scheduler": {"app"},
    "migrate": {"app"},
    "postgres": {"app"},
    "redis": {"app"},
    "sandbox-manager": {"app", "exec"},
    "docker-socket-proxy": {"exec"},
    "egress-proxy": {"kk-egress", "egress-out"},
}
PROXY_ALLOWED = {"CONTAINERS", "IMAGES", "POST", "PING", "VERSION"}  # ADR-026 (3)
PROXY_FLAGS = PROXY_ALLOWED | {
    "INFO", "AUTH", "BUILD", "COMMIT", "CONFIGS", "DISTRIBUTION", "EVENTS", "EXEC", "GRPC", "NETWORKS",
    "NODES", "PLUGINS", "SECRETS", "SERVICES", "SESSION", "SWARM", "SYSTEM", "TASKS", "VOLUMES",
}
# DEPLOYMENT §2: cpus, memory (bytes), pids.
LIMITS = {
    "caddy": (0.5, 256 << 20, 256),
    "web": (0.25, 128 << 20, 128),
    "api": (1.0, 1536 << 20, 512),
    "migrate": (0.5, 512 << 20, 256),
    "worker": (2.0, 2 << 30, 1024),
    "scheduler": (0.25, 256 << 20, 128),
    "postgres": (1.0, 2 << 30, 512),
    "redis": (0.5, 512 << 20, 128),
    "sandbox-manager": (0.5, 512 << 20, 256),
    "docker-socket-proxy": (0.25, 128 << 20, 128),
    "egress-proxy": (0.25, 128 << 20, 128),
}
BACKEND = {"api", "worker", "scheduler", "migrate"}


def secrets_from_env_file() -> dict[str, str]:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {k: v for k, v in values.items() if k.endswith(("_KEY", "_TOKEN", "_PASSWORD"))}


def database_user(url: str) -> str:
    return url.split("://", 1)[1].split(":", 1)[0]


def render(*profiles: str) -> dict:
    flags = [arg for profile in profiles for arg in ("--profile", profile)]
    output = subprocess.run(
        ["docker", "compose", "-f", "infra/compose.yml", "--env-file", str(ENV_FILE), *flags, "config", "--format", "json"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(output)


class ComposePolicy(unittest.TestCase):
    """The whole production stack, egress profile included, so every rule also covers egress-proxy."""

    config: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = render("egress")
        cls.services = cls.config["services"]

    def test_service_set(self) -> None:
        self.assertEqual(set(self.services), SERVICES)

    def test_only_caddy_publishes(self) -> None:
        published = {name for name, svc in self.services.items() if svc.get("ports")}
        self.assertEqual(published, {"caddy"})
        ports = {(p["published"], p.get("protocol", "tcp")) for p in self.services["caddy"]["ports"]}
        self.assertEqual(ports, {("80", "tcp"), ("443", "tcp"), ("443", "udp")})

    def test_socket_only_in_proxy(self) -> None:
        for name, svc in self.services.items():
            for volume in svc.get("volumes", []):
                if "docker.sock" in str(volume.get("source", "")):
                    self.assertEqual(name, "docker-socket-proxy", f"{name} mounts the Docker socket")
                    self.assertTrue(volume.get("read_only"), "the socket must be mounted read-only")
        proxy_sources = [v.get("source") for v in self.services["docker-socket-proxy"]["volumes"]]
        self.assertEqual(proxy_sources, ["/var/run/docker.sock"])

    def test_exec_network_members(self) -> None:
        members = {name for name, svc in self.services.items() if "exec" in svc.get("networks", {})}
        self.assertEqual(members, {"sandbox-manager", "docker-socket-proxy"})

    def test_network_membership(self) -> None:
        for name, expected in NETWORKS.items():
            self.assertEqual(set(self.services[name].get("networks", {})), expected, name)

    def test_internal_networks(self) -> None:
        networks = self.config["networks"]
        for name in ("edge", "app", "exec", "kk-egress"):
            self.assertTrue(networks[name].get("internal"), f"{name} must be internal")
        for name in ("public", "outbound", "egress-out"):
            self.assertFalse(networks[name].get("internal", False), f"{name} must reach outside")

    def test_hardening(self) -> None:
        for name, svc in self.services.items():
            with self.subTest(service=name):
                self.assertIs(svc.get("read_only"), True)
                self.assertEqual(svc.get("cap_drop"), ["ALL"])
                self.assertIn("no-new-privileges:true", svc.get("security_opt", []))
                self.assertFalse(svc.get("privileged", False))
                for key in ("pid", "ipc", "network_mode", "userns_mode", "devices"):
                    self.assertNotIn(key, svc)
                self.assertEqual(svc.get("cap_add", []), ["NET_BIND_SERVICE"] if name == "caddy" else [])
                self.assertEqual(svc["logging"]["driver"], "json-file")
                self.assertEqual(svc["logging"]["options"]["max-size"], "10m")

    def test_non_root_users(self) -> None:
        for name in BACKEND | {"sandbox-manager"}:
            self.assertIsNone(self.services[name].get("user"), f"{name}: the image sets uid 10001")
        expected = {"web": "101:101", "postgres": "70:70", "redis": "999:999", "egress-proxy": "100:101"}
        for name, user in expected.items():
            self.assertEqual(self.services[name].get("user"), user, name)

    def test_socket_proxy_flags(self) -> None:
        env = self.services["docker-socket-proxy"]["environment"]
        flags = {k: v for k, v in env.items() if k not in ("LOG_LEVEL", "DISABLE_IPV6")}
        self.assertEqual(set(flags), PROXY_FLAGS)
        enabled = {k for k, v in flags.items() if v == "1"}
        self.assertEqual(enabled, PROXY_ALLOWED)
        self.assertEqual(flags["INFO"], "0")

    def test_sandbox_manager_reaches_docker_only_through_the_proxy(self) -> None:
        env = self.services["sandbox-manager"]["environment"]
        self.assertEqual(env["DOCKER_HOST"], "tcp://docker-socket-proxy:2375")
        self.assertEqual(env["APP_ENV"], "production")
        self.assertEqual(self.services["sandbox-manager"]["depends_on"]["docker-socket-proxy"]["condition"], "service_healthy")

    def test_workspace_path_identical(self) -> None:
        for name in ("api", "worker", "sandbox-manager"):
            svc = self.services[name]
            root = svc["environment"]["WORKSPACE_ROOT"]
            binds = [v for v in svc["volumes"] if v.get("target") == root]
            self.assertEqual(len(binds), 1, f"{name}: workspace bind at WORKSPACE_ROOT")
            self.assertEqual(binds[0]["source"], root, f"{name}: host and container paths must match")
            # Rendering differs by compose version (v2 drops false, v5 drops the default true), so the
            # rendered flag may be absent; test_workspace_bind_never_creates_the_host_path checks the source.
            self.assertIn(binds[0].get("bind", {}).get("create_host_path"), (False, None))

    def test_workspace_bind_never_creates_the_host_path(self) -> None:
        """ADR-005: the workspace folder must already exist (owner 10001, 0750); compose never makes it."""
        lines = (ROOT / "infra" / "compose.yml").read_text().splitlines()
        sources = [i for i, line in enumerate(lines) if line.strip() == "source: ${WORKSPACE_HOST_PATH:-/srv/kodekantor/workspaces}"]
        self.assertEqual(len(sources), 2, "backend volumes anchor + sandbox-manager")
        for index in sources:
            block = [line.strip() for line in lines[index : index + 3]]
            self.assertIn("bind: {create_host_path: false}", block)

    def test_anthropic_key_only_in_worker(self) -> None:
        holders = {name for name, svc in self.services.items() if svc.get("environment", {}).get("ANTHROPIC_API_KEY")}
        self.assertEqual(holders, {"worker"})

    def test_secrets_per_service(self) -> None:
        """DEPLOYMENT §7: each service gets only what it uses; migrate and scheduler get placeholders where
        Settings demands a value they never use."""
        allowed = {
            "SECRET_KEY": {"api", "worker"},
            "SANDBOX_MANAGER_TOKEN": {"api", "worker", "sandbox-manager"},
            "POSTGRES_PASSWORD": {"migrate", "postgres"},  # the owner: migrations only
            "DB_APP_PASSWORD": {"api", "worker", "scheduler", "postgres"},  # the runtime role
            "REDIS_PASSWORD": {"api", "worker", "scheduler", "redis"},  # inside REDIS_URL; migrate needs no broker
            "ANTHROPIC_API_KEY": {"worker"},
        }
        for key, value in secrets_from_env_file().items():
            holders = {
                name for name, svc in self.services.items()
                if any(value in str(v) for v in svc.get("environment", {}).values())
            }
            self.assertEqual(holders, allowed[key], key)

    def test_runtime_services_use_the_runtime_role(self) -> None:
        """Only migrate connects as the owner; the API refuses to start as one (audit guard, T10)."""
        for name in ("api", "worker", "scheduler"):
            self.assertEqual(database_user(self.services[name]["environment"]["DATABASE_URL"]), "kodekantor_app")
        self.assertEqual(database_user(self.services["migrate"]["environment"]["DATABASE_URL"]), "kodekantor")
        self.assertIn("grant-runtime-role --role kodekantor_app", " ".join(self.services["migrate"]["command"]))

    def test_resource_limits(self) -> None:
        for name, (cpus, memory, pids) in LIMITS.items():
            limits = self.services[name]["deploy"]["resources"]["limits"]
            with self.subTest(service=name):
                self.assertEqual(float(limits["cpus"]), cpus)
                self.assertEqual(int(limits["memory"]), memory)
                self.assertEqual(int(limits["pids"]), pids)

    def test_images_pinned_or_built_locally(self) -> None:
        for name, svc in self.services.items():
            with self.subTest(service=name):
                if "build" in svc:
                    self.assertTrue(svc["image"].startswith("kodekantor/"))
                    self.assertEqual(svc.get("pull_policy"), "never")
                else:
                    self.assertRegex(svc["image"], r"@sha256:[0-9a-f]{64}$")

    def test_start_order_and_health(self) -> None:
        for name in ("api", "worker", "scheduler"):
            self.assertEqual(
                self.services[name]["depends_on"]["migrate"]["condition"], "service_completed_successfully", name
            )
        self.assertEqual(self.services["migrate"]["restart"], "no")
        for name, svc in self.services.items():
            if name != "migrate":
                self.assertIn("healthcheck", svc, name)
        self.assertIn("/api/v1/health/ready", " ".join(self.services["api"]["healthcheck"]["test"]))

    def test_egress_networks(self) -> None:
        """DEPLOYMENT §3: kk-egress holds egress-proxy and the task containers sandbox-manager attaches by name;
        egress-out is the proxy's only way out. No compose service but the proxy joins either."""
        networks = self.config["networks"]
        self.assertEqual(networks["kk-egress"]["name"], "kk-egress", "fixed name: no compose project prefix")
        # Review AG-13 WP-1.5b M1: without this the host owns the bridge gateway address, and task code on
        # kk-egress reaches every host service listening on 0.0.0.0.
        self.assertEqual(networks["kk-egress"].get("driver_opts"), {"com.docker.network.bridge.inhibit_ipv4": "true"})
        self.assertIs(networks["kk-egress"].get("enable_ipv6"), False)
        for network in ("kk-egress", "egress-out"):
            members = {name for name, svc in self.services.items() if network in svc.get("networks", {})}
            self.assertEqual(members, {"egress-proxy"}, network)

    def test_sandbox_manager_egress_network(self) -> None:
        """sandbox-manager sets NetworkMode to SANDBOX_EGRESS_NETWORK; it must name the network compose made."""
        env = self.services["sandbox-manager"]["environment"]
        self.assertEqual(env["SANDBOX_EGRESS_NETWORK"], self.config["networks"]["kk-egress"]["name"])

    def test_egress_proxy_service(self) -> None:
        """ADR-007: default deny; CONNECT to the three registries on 443 only (spec WP-1.5b)."""
        svc = self.services["egress-proxy"]
        self.assertEqual(svc.get("profiles"), ["egress"])
        self.assertEqual(svc["healthcheck"]["test"], ["CMD", "nc", "-z", "127.0.0.1", "3128"])
        self.assertNotIn("environment", svc)
        folder = ROOT / "infra" / "egress-proxy"
        allowlist = [line for line in (folder / "allowlist").read_text().splitlines() if line.strip()]
        self.assertEqual(
            allowlist,
            [r"^pypi\.org:443$", r"^files\.pythonhosted\.org:443$", r"^registry\.npmjs\.org:443$"],
        )
        conf = {}
        for line in (folder / "tinyproxy.conf").read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                key, _, value = line.partition(" ")
                conf.setdefault(key, []).append(value.strip())
        for key, value in {
            "FilterURLs": "On", "FilterType": "ere", "FilterDefaultDeny": "Yes", "ConnectPort": "443",
            "DisableViaHeader": "Yes", "XTinyproxy": "No",
            "LogLevel": "Connect",  # every requested target is logged, allowed or refused (review M1.5b L1)
        }.items():
            self.assertEqual(conf.get(key), [value], key)
        for key in ("Upstream", "Allow", "Deny", "BasicAuth", "ReverseOnly", "ReversePath"):
            self.assertNotIn(key, conf)

    def test_api_trusts_only_caddy(self) -> None:
        """Review AG-13 WP-1.5a L1: proxy headers are believed only from Caddy's fixed address on edge."""
        ipam = self.config["networks"]["edge"]["ipam"]["config"][0]
        subnet, dynamic = ipaddress.ip_network(ipam["subnet"]), ipaddress.ip_network(ipam["ip_range"])
        caddy_ip = ipaddress.ip_address(self.services["caddy"]["networks"]["edge"]["ipv4_address"])
        self.assertIn(caddy_ip, subnet)
        self.assertTrue(dynamic.subnet_of(subnet), "ip_range must lie inside the subnet")
        # Docker hands out addresses from ip_range in start order; a fixed address inside it may be taken.
        self.assertNotIn(caddy_ip, dynamic)
        self.assertEqual(self.services["api"]["environment"]["FORWARDED_ALLOW_IPS"], str(caddy_ip))
        for name, svc in self.services.items():
            if name != "api":
                self.assertNotIn("FORWARDED_ALLOW_IPS", svc.get("environment", {}), name)

    def test_backend_image_trusts_no_proxy(self) -> None:
        """uvicorn trusts only FORWARDED_ALLOW_IPS (default 127.0.0.1); the image must not override it."""
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
        self.assertNotIn("forwarded-allow-ips", dockerfile)
        self.assertIn('"--proxy-headers"', dockerfile)

    def test_dockerfile_bases_pinned(self) -> None:
        listed = subprocess.run(
            ["git", "ls-files", "*Dockerfile*"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.split()
        self.assertIn("infra/egress-proxy/Dockerfile", listed)
        for path in listed:
            for line in (ROOT / path).read_text().splitlines():
                if line.startswith("FROM "):
                    with self.subTest(dockerfile=path):
                        self.assertRegex(line.split()[1], r"@sha256:[0-9a-f]{64}$")


class EgressOffByDefault(unittest.TestCase):
    """RUNBOOK §11, ADR-007: without the egress profile there is no proxy and no kk-egress at all."""

    def test_no_proxy_without_profile(self) -> None:
        config = render()
        self.assertNotIn("egress-proxy", config["services"])
        self.assertNotIn("kk-egress", config["networks"])
        self.assertNotIn("egress-out", config["networks"])
        self.assertEqual(config["services"]["sandbox-manager"]["environment"]["SANDBOX_EGRESS"], "off")


if __name__ == "__main__":
    unittest.main()
