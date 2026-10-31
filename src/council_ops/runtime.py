"""Invoke Docker with argument arrays and bounded, secret-free diagnostics."""

import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager

from .config import OperationDeferred, OperationError, atomic_write, read_settings


class Runtime:
    def __init__(self, settings):
        self.settings = settings
        self.guard = None

    def check_protected(self):
        if self.guard is not None:
            self.guard.check()

    def preflight(self):
        host = os.environ.get("DOCKER_HOST", "")
        if host and not host.startswith("unix://"):
            raise OperationError("Run operations against the local Docker daemon only.")
        context = json.loads(self.run(["docker", "context", "inspect"]))[0]
        if not context["Endpoints"]["docker"]["Host"].startswith("unix://"):
            raise OperationError("Remote Docker contexts are not supported by host-side backups.")

    def compose_source(self):
        active = self.settings.env_file.with_name("active-compose.yaml")
        if active.is_symlink():
            raise OperationError("Refusing a symlinked active Compose source.")
        return active if active.exists() else self.settings.repository / "compose.yaml"

    def activate_source(self, source):
        atomic_write(self.settings.env_file.with_name("active-compose.yaml"), source.read_bytes())

    def adopt_release(self, revision):
        import re

        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise OperationError("A full deployed Git revision is required.")
        active = self.settings.env_file.with_name("active-compose.yaml")
        if active.exists():
            raise OperationError("An active Compose source is already recorded.")
        recorded = self.settings.env_file.with_name("revision").read_text().strip()
        _, labels = self.image()
        if (
            recorded != revision
            or labels.get("org.opencontainers.image.revision", revision) != revision
        ):
            raise OperationError("The requested revision differs from the deployed release record.")
        source = self.run(["git", "show", revision + ":compose.yaml"])
        atomic_write(active, (source + "\n").encode())

    def require_idle(self):
        if not self.inspect()["State"]["Running"]:
            return
        source = (
            "import json; from gateway.status import read_runtime_status,runtime_status_is_stale; "
            "s=read_runtime_status() or {}; "
            "print(json.dumps({'fresh':not runtime_status_is_stale(s),"
            "'agents':s.get('active_agents'),'work':bool(s.get('active_work'))}))"
        )
        status = json.loads(
            self.compose(
                "exec",
                "-T",
                "--user",
                "hermes",
                "hermes",
                "/opt/hermes/.venv/bin/python",
                "-c",
                source,
            )
        )
        if not status["fresh"] or type(status["agents"]) is not int or status["agents"] < 0:
            raise OperationError("Hermes status is stale or invalid; inspect readiness.")
        if status["agents"] or status["work"]:
            raise OperationDeferred("Hermes is busy; retry maintenance when idle.")
        # Inspect locally; never emit command lines, which may include private prompts.
        processes = self.run(["docker", "top", self.container(), "-eo", "pid,args"])
        if any(
            marker in processes
            for marker in (
                "hermes model",
                "configure-drive.py",
                "switch-telegram-bot.py",
                "port=CALLBACK_PORT",
            )
        ):
            raise OperationDeferred("Finish interactive account setup before maintenance.")
        source = (
            "import sqlite3; from pathlib import Path; "
            "root=Path('/opt/data/plugin-data/council-secretary'); "
            "counts=[sqlite3.connect(p.as_uri()+'?mode=ro',uri=True).execute(\"SELECT count(*) FROM jobs WHERE status IN ('queued','working','sending')\").fetchone()[0] "
            "for p in (root/'queue.sqlite3',root/'public-queue.sqlite3',root/'guest-queue.sqlite3') if p.exists()]; "
            "owner=root/'owner-guest.sqlite3'; "
            "pending_owner=sqlite3.connect(owner.as_uri()+'?mode=ro',uri=True).execute(\"SELECT count(*) FROM owner_jobs WHERE status IN ('ack','running')\").fetchone()[0] if owner.exists() else 0; "
            "print(sum(counts)+pending_owner)"
        )
        pending = self.compose(
            "exec", "-T", "--user", "hermes", "hermes", "/opt/hermes/.venv/bin/python", "-c", source
        )
        if pending != "0":
            raise OperationDeferred("Secretary replies are pending; retry maintenance when idle.")
        from .linear_state import root

        path = root(self.settings) / "bridge/linear.sqlite3"
        if path.exists():
            import sqlite3
            from contextlib import closing

            if path.resolve() != path:
                raise OperationError("Refusing a symlinked Linear store.")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                count = db.execute(
                    "SELECT count(*) FROM jobs WHERE state NOT IN ('done','failed','cancelled')"
                ).fetchone()[0]
            if count:
                raise OperationDeferred(
                    "Linear tasks are active; finish or stop them before maintenance."
                )

    def run(self, arguments, *, timeout=180, interactive=False, environment=None):
        try:
            result = subprocess.run(
                arguments,
                cwd=self.settings.repository,
                env=environment,
                check=True,
                timeout=timeout,
                capture_output=not interactive,
                text=True,
            )
            return result.stdout.strip() if result.stdout else ""
        except (subprocess.SubprocessError, OSError) as error:
            # Docker output can contain interpolated settings or credential-bearing URLs.
            raise OperationError(
                f"Command failed ({arguments[0]}, {type(error).__name__}); inspect private server diagnostics."
            ) from None

    def compose(self, *arguments, timeout=180, image=None, interactive=False):
        if arguments and arguments[0] == "config" and arguments != ("config", "--quiet"):
            raise OperationError("Resolved production Compose configuration must not be printed.")
        environment = dict(os.environ)
        # The env file is authoritative; a stray caller variable cannot redirect volumes or auth.
        for key in list(environment):
            if key.startswith(("HERMES_", "COUNCIL_", "COMPOSE_")):
                environment.pop(key, None)
        if image is not None:
            environment["COUNCIL_HERMES_IMAGE"] = image
        return self.run(
            [
                "docker",
                "compose",
                "--project-name",
                "agent-council",
                "--env-file",
                str(self.settings.env_file),
                "--project-directory",
                str(self.settings.repository),
                "-f",
                str(self.compose_source()),
                *arguments,
            ],
            timeout=timeout,
            environment=environment,
            interactive=interactive,
        )

    def container(self):
        identifiers = self.compose("ps", "--all", "--quiet", "hermes").splitlines()
        if len(identifiers) != 1:
            raise OperationError("Expected exactly one Hermes container.")
        return identifiers[0]

    def inspect(self):
        return json.loads(self.run(["docker", "inspect", self.container()]))[0]

    def image(self):
        inspected = self.inspect()
        identifier = inspected["Image"]
        metadata = json.loads(self.run(["docker", "image", "inspect", identifier]))[0]
        return identifier, metadata.get("Config", {}).get("Labels", {}) or {}

    def stop(self):
        reader = self.companion("telegram-reader")
        if reader:
            self.run(["docker", "stop", "--time", "10", reader])
        for _service, container, running in self.linear_companions():
            if running:
                self.run(["docker", "stop", "--time", "30", container])
        self.compose("stop", "--timeout", "30", "hermes")
        companion = self.companion()
        if companion:
            self.run(["docker", "stop", "--time", "15", companion])

    @contextmanager
    def frozen(self):
        """Freeze owned writers briefly; a host timer thaws them if this CLI dies."""
        containers = [self.container()]
        for service in ("secretary", "telegram-reader"):
            companion = self.companion(service)
            if companion:
                state = json.loads(self.run(["docker", "inspect", companion]))[0]["State"]
                if state["Running"]:
                    containers.append(companion)
        containers.extend(
            container for _service, container, running in self.linear_companions() if running
        )
        if any(not re.fullmatch(r"[a-f0-9]{12,64}", value) for value in containers):
            raise OperationError("Expected owned Docker container IDs.")
        unit = "agent-council-thaw-" + uuid.uuid4().hex
        # Schedule before freezing. The independent system manager survives a
        # killed CLI; an incomplete checkpoint never receives a manifest.
        self.run(
            [
                "systemd-run",
                "--quiet",
                "--collect",
                "--unit=" + unit,
                "--on-active=45s",
                "--timer-property=AccuracySec=1s",
                "/usr/bin/docker",
                "unpause",
                *containers,
            ]
        )
        paused = []
        try:
            deadline = time.monotonic() + 20
            for container in containers:
                paused.append(container)
                self.run(["docker", "pause", container], timeout=10)
            yield deadline
            if time.monotonic() >= deadline:
                raise OperationError("Snapshot freeze budget exceeded; checkpoint discarded.")
        finally:
            failures = []
            for container in reversed(paused):
                try:
                    self.run(["docker", "unpause", container], timeout=10)
                except OperationError:
                    failures.append(container)
            if not failures:
                self.run(["systemctl", "stop", unit + ".timer"])
            else:
                # Leave the independent recovery timer armed.
                raise OperationError("Could not thaw a container; recovery watchdog remains armed.")

    def companion(self, service="secretary"):
        from .linear_state import SERVICES

        if service not in ("secretary", "telegram-reader", *SERVICES):
            raise OperationError("Unrecognized owned companion service.")
        identifiers = self.run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=agent-council",
                "--filter",
                "label=com.docker.compose.service=" + service,
            ]
        ).splitlines()
        if len(identifiers) > 1:
            raise OperationError("Expected at most one owned service container.")
        return identifiers[0] if identifiers else None

    def linear_companions(self):
        from .linear_state import SERVICES, root

        if not root(self.settings).exists():
            return []
        result = []
        for service in SERVICES:
            container = self.companion(service)
            if container:
                running = json.loads(self.run(["docker", "inspect", container]))[0]["State"][
                    "Running"
                ]
                result.append((service, container, running))
        return result

    def running_services(self):
        companion = self.companion()
        if (
            companion
            and json.loads(self.run(["docker", "inspect", companion]))[0]["State"]["Running"]
        ):
            services = ["hermes", "secretary"]
        else:
            services = ["hermes"]
        services.extend(
            service for service, _container, running in self.linear_companions() if running
        )
        return services

    def start(self):
        values = read_settings(self.settings.env_file)
        services = ["hermes"]
        if values.get("COUNCIL_SECRETARY_ENABLED") == "true":
            if "\n  secretary:\n" not in self.compose_source().read_text():
                raise OperationError("The active release does not contain the secretary service.")
            services.append("secretary")
        if values.get("COUNCIL_LINEAR_ENABLED") == "true":
            if "\n  linear-adapter:\n" not in self.compose_source().read_text():
                raise OperationError("The active release does not contain Linear services.")
            services.extend(["linear-adapter", "linear-executor"])
            if values.get("COUNCIL_LINEAR_DEXTER_ENABLED") == "true":
                services.append("dexter-executor")
            if values.get("COUNCIL_LINEAR_MARKETING_ENABLED") == "true":
                services.append("marketing-executor")
            if values.get("COUNCIL_LINEAR_PADME_ENABLED") == "true":
                services.append("padme-executor")
        self.compose(
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "--pull",
            "never",
            "--wait",
            "--wait-timeout",
            "150",
            *services,
        )

    def ready(self):
        self.compose(
            "exec",
            "-T",
            "--user",
            "hermes",
            "hermes",
            "hermes",
            "gateway",
            "ensure",
            "--timeout",
            "60",
        )
        if not getattr(self, "_linear_maintenance_depth", 0):
            from .linear_state import root

            (root(self.settings) / "bridge/runtime/maintenance").unlink(missing_ok=True)

    def revision(self):
        revision = self.run(["git", "rev-parse", "HEAD"])
        if self.run(["git", "status", "--porcelain"]):
            raise OperationError("Deploy only a clean Git checkout.")
        return revision
