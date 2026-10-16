import ipaddress
import os
import re
from urllib.parse import urlparse

from hosts import load_customer_cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _getenv(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


def _getenv_bool(name: str) -> bool:
    value = _getenv(name).lower()
    if value in {"", "0", "false", "no"}:
        return False
    if value in {"1", "true", "yes"}:
        return True
    raise ValueError(f"{name} must be true or false")


def _getenv_all_or_nothing(*names: str, error: str) -> tuple[dict[str, str], bool]:
    """Load env keys as a group: all empty → disabled; partial → error; all set → enabled."""
    values = {name: _getenv(name) for name in names}
    filled = sum(1 for value in values.values() if value)
    if filled == 0:
        return values, False
    if filled != len(names):
        raise ValueError(error)
    return values, True


def _lookup(cfg: dict, *keys: str):
    cur = cfg
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _required(cfg: dict, *keys: str) -> str:
    path = ".".join(keys)
    value = _lookup(cfg, *keys)

    if value is None or isinstance(value, (dict, list)):
        raise ValueError(f"infra.yaml: {path} required")

    value = str(value).strip()
    if not value:
        raise ValueError(f"infra.yaml: {path} required")

    return value


def _optional(cfg: dict, *keys: str, default: str = "") -> str:
    value = _lookup(cfg, *keys)

    if value is None:
        return default

    if isinstance(value, (dict, list)):
        raise ValueError(f"infra.yaml: {'.'.join(keys)} must be a string")

    return str(value).strip() or default


def _mapping(value, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"infra.yaml: {path} must be a mapping")
    return value


def _valid_probe_hostname(value: str | None) -> bool:
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    value = value.removesuffix(".")
    if not value or len(value) > 253:
        return False
    return all(
        re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
            label,
        )
        for label in value.split(".")
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

admin_user = "platform"
ssh_user = admin_user
breakglass_user = "breakglass"
platform_root = "/opt/platform"
deploy_user = "deploy"

default_ports = {
    "ssh": {"port": 22, "proto": "tcp", "scope": "public"},
    "http": {"port": 80, "proto": "tcp", "scope": "netbird"},
    "https": {"port": 443, "proto": "tcp", "scope": "netbird"},
    "node_exporter": {
        "port": 9100,
        "proto": "tcp",
        "scope": "netbird",
        "when": "monitoring_enabled",
    },
    "prometheus": {
        "port": 9090,
        "proto": "tcp",
        "scope": "netbird",
        "when": "is_monitor_host",
    },
    "alertmanager": {
        "port": 9093,
        "proto": "tcp",
        "scope": "netbird",
        "when": "is_monitor_host",
    },
    "blackbox_exporter": {
        "port": 9115,
        "proto": "tcp",
        "scope": "netbird",
        "when": "is_monitor_probe_host",
    },
}
cloudflare_api = "https://api.cloudflare.com/client/v4"

ssh_public_key = _getenv("SSH_PUBLIC_KEY")
deploy_public_key = _getenv("DEPLOY_PUBLIC_KEY")
deploy_sops_age_key = _getenv("DEPLOY_SOPS_AGE_KEY")
cf_admin_token = _getenv("CF_ADMIN_TOKEN")

_restic_primary, restic_enabled = _getenv_all_or_nothing(
    "RESTIC_PRIMARY_REPOSITORY",
    "RESTIC_PRIMARY_ACCESS_KEY_ID",
    "RESTIC_PRIMARY_SECRET_ACCESS_KEY",
    error=(
        "Set RESTIC_PRIMARY_REPOSITORY, RESTIC_PRIMARY_ACCESS_KEY_ID, "
        "RESTIC_PRIMARY_SECRET_ACCESS_KEY (or omit all for no backups)"
    ),
)
restic_primary_repository = _restic_primary["RESTIC_PRIMARY_REPOSITORY"]
restic_primary_access_key_id = _restic_primary["RESTIC_PRIMARY_ACCESS_KEY_ID"]
restic_primary_secret_access_key = _restic_primary["RESTIC_PRIMARY_SECRET_ACCESS_KEY"]
restic_primary_region = _getenv("RESTIC_PRIMARY_REGION", "auto")

_restic_offsite, restic_offsite_enabled = _getenv_all_or_nothing(
    "RESTIC_OFFSITE_REPOSITORY",
    "RESTIC_OFFSITE_ACCESS_KEY_ID",
    "RESTIC_OFFSITE_SECRET_ACCESS_KEY",
    error=(
        "Set RESTIC_OFFSITE_REPOSITORY, RESTIC_OFFSITE_ACCESS_KEY_ID, "
        "RESTIC_OFFSITE_SECRET_ACCESS_KEY (or omit all)"
    ),
)
restic_offsite_repository = _restic_offsite["RESTIC_OFFSITE_REPOSITORY"]
restic_offsite_access_key_id = _restic_offsite["RESTIC_OFFSITE_ACCESS_KEY_ID"]
restic_offsite_secret_access_key = _restic_offsite["RESTIC_OFFSITE_SECRET_ACCESS_KEY"]
restic_offsite_region = _getenv("RESTIC_OFFSITE_REGION", "auto")

restic_password_primary_key = "RESTIC_PRIMARY_PASSWORD"
restic_password_offsite_key = "RESTIC_OFFSITE_PASSWORD"
restic_password_primary = _getenv(restic_password_primary_key)
restic_password_offsite = _getenv(restic_password_offsite_key)
restic_rotate_password = _getenv_bool("RESTIC_ROTATE_PASSWORD")

bootstrap_pull_policy = _getenv("BOOTSTRAP_PULL_POLICY", "missing")
acme_email = _getenv("ACME_EMAIL")
cf_acme_token = _getenv("CF_ACME_TOKEN")
platform_deploy_username = _getenv("PLATFORM_DEPLOY_USERNAME")
platform_deploy_token = _getenv("PLATFORM_DEPLOY_TOKEN")

telegram_alerter_bot_token = _getenv("TELEGRAM_ALERTER_BOT_TOKEN")
healthchecks_ping_key = _getenv("HEALTHCHECKS_PING_KEY")


# ---------------------------------------------------------------------------
# Customer (infra.yaml)
# ---------------------------------------------------------------------------

_cfg = _mapping(load_customer_cfg(), "root")

monitor_host = _optional(_cfg, "monitor_host")
monitor_probe_host = _optional(_cfg, "monitor_probe_host")
monitor_ui = _optional(_cfg, "monitor_ui")

_monitor_probes = _cfg.get("monitor_probes") or {}
_mapping(_monitor_probes, "monitor_probes")
monitor_probes = {}
for service, probe in _monitor_probes.items():
    service = str(service).strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", service):
        raise ValueError(
            "infra.yaml: monitor_probes keys must match [a-zA-Z0-9_-]+"
        )

    if isinstance(probe, str):
        module, target = "http_2xx", probe.strip()
    elif isinstance(probe, dict):
        module = str(probe.get("module", "")).strip()
        target = str(probe.get("target", "")).strip()
        unknown = set(probe) - {"module", "target"}
        if unknown:
            raise ValueError(
                f"infra.yaml: monitor_probes.{service} has unknown keys: "
                f"{', '.join(sorted(unknown))}"
            )
    else:
        module, target = "", ""

    if module not in {"http_2xx", "tcp_connect"}:
        raise ValueError(
            f"infra.yaml: monitor_probes.{service}.module must be "
            "http_2xx or tcp_connect"
        )

    if module == "http_2xx":
        parsed = urlparse(target)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        valid = (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
            and _valid_probe_hostname(parsed.hostname)
            and port != -1
            and (port is None or port > 0)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
        )
    else:
        parsed = urlparse(f"//{target}")
        try:
            port = parsed.port
        except ValueError:
            port = None
        valid = (
            bool(parsed.hostname)
            and _valid_probe_hostname(parsed.hostname)
            and port is not None
            and port > 0
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )

    if not target or not valid:
        raise ValueError(
            f"infra.yaml: monitor_probes.{service}.target is invalid for {module}"
        )
    monitor_probes[service] = {"module": module, "target": target}

if monitor_probes and not (monitor_host and monitor_probe_host):
    raise ValueError(
        "infra.yaml: monitor_probes requires monitor_host and monitor_probe_host"
    )

gitlab_group = _required(_cfg, "gitlab", "group")
gitlab_registry = _required(_cfg, "gitlab", "registry")
gitlab_caddy_image = _required(_cfg, "gitlab", "caddy_image")

dns_zone = _required(_cfg, "dns", "zone")
acme_zone = _required(_cfg, "dns", "acme_zone")

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

_tg = _cfg.get("telegram")

if _tg is None:
    telegram_chat_id = ""
    telegram_threads = {}
else:
    _mapping(_tg, "telegram")

    telegram_chat_id = _required(_cfg, "telegram", "chat_id")

    _threads = _tg.get("threads") or {}
    _mapping(_threads, "telegram.threads")

    telegram_threads = {}

    for key, value in _threads.items():
        if value is None or isinstance(value, (dict, list)):
            raise ValueError(
                f"infra.yaml: telegram.threads.{key} must be a string"
            )

        telegram_threads[str(key).strip()] = str(value).strip()

telegram_alerting_enabled = bool(
    telegram_alerter_bot_token and telegram_chat_id
)
