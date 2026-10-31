"""Host-only, deduplicated owner alerts; no prompts or raw provider errors."""

import fcntl
import json
import os
import re
import shutil
import time
import urllib.request
from contextlib import contextmanager

from telegram_secretary.policy import pinned_owner

from .config import OperationError, atomic_write
from .doctor import backup_status, doctor, instruction_status, research_status
from .runtime import Runtime

LABELS = {
    "deployment": "Не удалось проверить состояние Docker и изоляцию сервисов.",
    "gateway": "Основной сервис недоступен или не прошёл проверку готовности.",
    "secretary": "Сервис ответов недоступен или его политика не готова.",
    "linear": "Исполнители Linear недоступны или их подключение не готово.",
    "business_queue": "Очередь личных ответов недоступна или задерживается.",
    "public_queue": "Очередь публичного бота недоступна или задерживается.",
    "guest_queue": "Очередь гостевых вызовов недоступна или задерживается.",
    "provider_business": "Последняя генерация в личном канале завершилась ошибкой провайдера.",
    "provider_public": "Последняя генерация публичного бота завершилась ошибкой провайдера.",
    "backup": "Нет свежей проверенной резервной копии R2 или отключено расписание.",
    "instructions": "Обновление инструкций остановлено, отклонено или требует сверки файлов.",
    "disk": "На сервере осталось мало свободного места.",
    "research": "Сохранение исследований требует внимания: очередь задерживается или автоматизация недоступна.",
}
NAMES = {
    "deployment": "Проверка развёртывания",
    "gateway": "Основной сервис",
    "secretary": "Сервис ответов",
    "linear": "Агенты Linear",
    "business_queue": "Очередь личных ответов",
    "public_queue": "Очередь публичного бота",
    "guest_queue": "Гостевые вызовы",
    "provider_business": "Провайдер личных ответов",
    "provider_public": "Провайдер публичного бота",
    "backup": "Резервные копии R2",
    "instructions": "Обновление инструкций",
    "disk": "Свободное место",
    "research": "Сохранение исследований",
}


class ProbeRuntime(Runtime):
    def run(self, arguments, *, timeout=20, **kwargs):
        return super().run(arguments, timeout=min(timeout, 20), **kwargs)


def private_json(path):
    if (
        path.resolve() != path
        or not path.is_file()
        or path.stat().st_uid != os.geteuid()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 1048576
    ):
        raise OperationError("Expected a private operator-owned monitoring file.")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise OperationError("Invalid monitoring state.")
    return value


def literal_value(path, key, *, owner):
    """Read one native dotenv value literally; never evaluate its other settings."""
    if (
        path.resolve() != path
        or not path.is_file()
        or path.stat().st_uid not in {owner, 0}
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 1048576
    ):
        raise OperationError("Expected a private profile environment file.")
    values = []
    for line in path.read_text().splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and name.strip().removeprefix("export ") == key:
            value = value.strip()
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            values.append(value)
    if len(values) != 1 or not isinstance(values[0], str):
        raise OperationError("The required monitoring credential is absent or ambiguous.")
    return values[0]


def configure(runtime):
    settings = runtime.settings
    owner = pinned_owner(settings.data_dir)
    if owner is None:
        raise OperationError("Complete verified owner sign-in before configuring notifications.")
    token = literal_value(
        settings.data_dir / ".env", "TELEGRAM_BOT_TOKEN", owner=int(settings.values["HERMES_UID"])
    )
    if not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{30,128}", token):
        raise OperationError("Configure the existing Telegram bot before notifications.")
    value = {
        "schema": 1,
        "owner_id": owner,
        "watch_backups": backup_status(settings)["configured"],
        "watch_instructions": instruction_status(settings.data_dir)["configured"],
        "watch_research": research_status(settings)["configured"],
    }
    path = settings.env_file.with_name("monitor.json")
    if path.exists() and private_json(path).get("owner_id") != owner:
        raise OperationError("Reconcile the configured monitoring owner explicitly.")
    atomic_write(path, (json.dumps(value) + "\n").encode())
    return {
        "monitoring_configured": True,
        "destination": "pinned_owner",
        "credentials_copied": False,
    }


def notify(settings, configuration, text):
    owner = configuration["owner_id"]
    if type(owner) is not int or pinned_owner(settings.data_dir, expected=str(owner)) != owner:
        raise OperationError("Monitoring destination does not match the pinned owner.")
    token = literal_value(
        settings.data_dir / ".env", "TELEGRAM_BOT_TOKEN", owner=int(settings.values["HERMES_UID"])
    )
    if not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{30,128}", token):
        raise OperationError("Invalid notification credential.")
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=json.dumps({"chat_id": owner, "text": text, "disable_notification": False}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=10) as response:
            result = json.loads(response.read(65536))
        if (
            result.get("ok") is not True
            or result.get("result", {}).get("chat", {}).get("id") != owner
        ):
            raise ValueError("Notification was not confirmed")
    except Exception:
        raise OperationError(
            "Owner notification failed; private transport details omitted."
        ) from None


def issues(report, configuration, *, free_bytes, total_bytes):
    result = {}
    if report.get("health") != "healthy" or report.get("status") != "running":
        result["gateway"] = "not_ready"
    secretary = report.get("secretary", {})
    linear = report.get("linear", {})
    if linear.get("configured") and (
        not linear.get("available")
        or (
            linear.get("enabled")
            and (
                not linear.get("available")
                or linear.get("installed_agents") != linear.get("registered_agents")
                or not linear.get("services")
                or any(
                    item.get("health") != "healthy" or item.get("status") != "running"
                    for item in linear.get("services", {}).values()
                )
            )
        )
    ):
        result["linear"] = "not_ready"
    if secretary.get("enabled"):
        if not secretary.get("policy_valid") or secretary.get("worker_health") != "healthy":
            result["secretary"] = "not_ready"
        if not secretary.get("guest_policy_valid", True):
            result["secretary"] = "not_ready"
        for route in ("business", "public", "guest"):
            queue = secretary.get("queues", {}).get(route, {})
            if queue.get("enabled") and (
                not queue.get("available")
                or queue.get("oldest_pending_age_seconds", 0)
                > {"business": 180, "public": 600, "guest": 150}[route]
            ):
                result[route + "_queue"] = "delayed"
            outcome = secretary.get("generation", {}).get("last_outcomes", {}).get(route, {})
            if outcome.get("outcome") in {
                "provider_auth",
                "provider_quota",
                "provider_timeout",
                "model_unavailable",
                "invalid_reply",
            }:
                result["provider_" + route] = outcome["outcome"]
    backups = report.get("backups", {})
    if configuration.get("watch_backups") and (
        not backups.get("configured")
        or not backups.get("last_verified")
        or backups.get("age_seconds", 129601) > 36 * 3600
        or backups.get("timer", {}).get("ActiveState") != "active"
        or backups.get("service", {}).get("Result", "success") != "success"
    ):
        result["backup"] = "stale_or_disabled"
    instructions = report.get("instructions", {})
    if configuration.get("watch_instructions") and (
        not instructions.get("available")
        or instructions.get("local_changes")
        or instructions.get("rejected_revision")
        or instructions.get("timer", {}).get("ActiveState") != "active"
        or instructions.get("service", {}).get("Result", "success") != "success"
    ):
        result["instructions"] = instructions.get("rejected_revision", "conflict_or_disabled")
    if free_bytes < max(1024**3, total_bytes // 20):
        result["disk"] = "low"
    research = report.get("research", {})
    if configuration.get("watch_research") and (
        not research.get("available")
        or research.get("oldest_pending_age_seconds", 0) > 1800
        or research.get("timer", {}).get("ActiveState") != "active"
    ):
        result["research"] = "filing_delayed_or_disabled"
    return result


def transition(previous, current):
    active = previous.get("active", {})
    if not isinstance(active, dict) or not set(active) <= LABELS.keys():
        raise OperationError("Invalid previous monitoring episode.")
    changed = sorted(key for key in current if active.get(key) != current[key])
    recovered = sorted(active.keys() - current.keys())
    lines = []
    if changed:
        lines.append(
            "Agent Council: требуется внимание.\n" + "\n".join(LABELS[key] for key in changed)
        )
    if recovered:
        lines.append("Agent Council: восстановлено.\n" + "\n".join(NAMES[key] for key in recovered))
    return "\n\n".join(lines)


@contextmanager
def lock(path):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a") as file:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def poll(settings, *, sender=notify, probe=None, clock=time.time, test=False):
    directory = settings.env_file.parent
    configuration = private_json(directory / "monitor.json")
    try:
        with lock(directory / "monitor.lock"), lock(directory / "operations.lock"):
            if test:
                sender(
                    settings,
                    configuration,
                    "Agent Council: проверка уведомлений. Канал владельца подключён.",
                )
                return {"notification_verified": True}
            try:
                if probe is None:
                    runtime = ProbeRuntime(settings)
                    runtime.preflight()
                    report = doctor(runtime)
                else:
                    report = probe()
                disk = shutil.disk_usage(settings.data_dir)
                current = issues(
                    report, configuration, free_bytes=disk.free, total_bytes=disk.total
                )
            except (OperationError, OSError, ValueError, KeyError, TypeError):
                current = {"deployment": "check_failed"}
            state_path = directory / "monitor-state.json"
            previous = (
                private_json(state_path) if state_path.exists() else {"schema": 1, "active": {}}
            )
            message = transition(previous, current)
            if message:
                sender(settings, configuration, message)
            atomic_write(
                state_path,
                (
                    json.dumps({"schema": 1, "active": current, "checked_at": clock()}) + "\n"
                ).encode(),
            )
            return {
                "monitoring_checked": True,
                "active_incidents": sorted(current),
                "notification_sent": bool(message),
            }
    except BlockingIOError:
        return {"monitoring_skipped": "maintenance_or_previous_check"}
