import io
import json
import shlex
from pathlib import Path

from pyinfra import host
from pyinfra.facts.files import File
from pyinfra.operations import files, server, systemd
from pyinfra.operations.util import any_changed

from hosts import MESH

platform_root = host.data.platform_root
core_root = Path(__file__).resolve().parents[2]
hostname = host.data.hostname
hosts = host.data.platform_hosts
hub = host.data.monitor_host
probe_host = host.data.monitor_probe_host
servers = [
    name
    for name, meta in hosts.items()
    if meta["role"] in MESH
]
monitoring_enabled = bool(hub and probe_host and hostname in servers)
is_hub = monitoring_enabled and hostname == hub
is_probe = monitoring_enabled and hostname == probe_host
heartbeat_enabled = bool(
    is_probe
    and host.data.healthchecks_ping_key
)


def _dirs(*items: tuple[str, str]) -> None:
    for path, mode in items:
        files.directory(
            name=f"Create {path}",
            path=path,
            user="root",
            group="root",
            mode=mode,
        )


def _put_compose(name: str, **kwargs) -> None:
    stack = f"{platform_root}/stacks/{name}"
    _dirs((stack, "755"))

    files.template(
        name=f"Render {name} compose",
        src=str(core_root / "stacks" / "labs" / name / "compose.yaml.j2"),
        dest=f"{stack}/compose.yaml",
        user="root",
        group="root",
        mode="644",
        **kwargs,
    )


def _up(name: str) -> None:
    project_dir = shlex.quote(f"{platform_root}/stacks/{name}")
    server.shell(
        name=f"Start {name}",
        commands=[
            f"docker compose --project-directory {project_dir} "
            "up -d --remove-orphans"
        ],
    )


def _down(name: str) -> None:
    project_dir_path = f"{platform_root}/stacks/{name}"
    compose_file_path = f"{project_dir_path}/compose.yaml"
    project_dir = shlex.quote(project_dir_path)
    compose_file = shlex.quote(compose_file_path)
    server.shell(
        name=f"Stop obsolete {name}",
        commands=[
            f"if [ -f {compose_file} ]; then "
            f"docker compose --project-directory {project_dir} "
            "down --remove-orphans; fi"
        ],
        _if=lambda: bool(host.get_fact(File, path=compose_file_path)),
    )
    files.file(
        name=f"Remove obsolete {name} compose",
        path=compose_file_path,
        present=False,
    )


if not heartbeat_enabled:
    heartbeat_paths = (
        "/etc/systemd/system/platform-monitor-heartbeat.timer",
        "/etc/systemd/system/platform-monitor-heartbeat.service",
        "/usr/local/bin/platform-monitor-heartbeat",
        f"{platform_root}/secrets/monitoring/healthchecks.env",
    )
    server.shell(
        name="Disable obsolete monitoring heartbeat",
        commands=[
            "if [ -e /etc/systemd/system/platform-monitor-heartbeat.timer ]; "
            "then systemctl disable --now platform-monitor-heartbeat.timer; fi",
            "if [ -e /etc/systemd/system/platform-monitor-heartbeat.service ]; "
            "then systemctl stop platform-monitor-heartbeat.service; fi",
        ],
        _if=lambda: any(
            host.get_fact(File, path=path) for path in heartbeat_paths
        ),
    )

    heartbeat_files = [
        files.file(
            name=f"Remove obsolete {Path(path).name}",
            path=path,
            present=False,
        )
        for path in heartbeat_paths
    ]

    systemd.daemon_reload(
        name="Reload systemd after monitoring heartbeat cleanup",
        _if=any_changed(*heartbeat_files),
    )


if not is_probe:
    _down("blackbox-exporter")

if not is_hub:
    _down("prometheus")
    prometheus_caddy_site = files.file(
        name="Remove obsolete Prometheus Caddy site",
        path=f"{platform_root}/stacks/caddy/config/sites/prometheus.caddy",
        present=False,
    )
    server.shell(
        name="Reload Caddy after Prometheus site cleanup",
        commands=[
            "docker exec caddy caddy validate "
            "--config /etc/caddy/Caddyfile --adapter caddyfile "
            "&& docker exec caddy caddy reload "
            "--config /etc/caddy/Caddyfile --adapter caddyfile"
        ],
        _if=prometheus_caddy_site.did_change,
    )

if not monitoring_enabled:
    _down("node-exporter")
    container_metric_paths = (
        "/etc/systemd/system/platform-containers.timer",
        "/etc/systemd/system/platform-containers.service",
        "/usr/local/bin/platform-containers",
    )
    server.shell(
        name="Disable obsolete container metrics timer",
        commands=[
            "if [ -e /etc/systemd/system/platform-containers.timer ]; "
            "then systemctl disable --now platform-containers.timer; fi",
            "if [ -e /etc/systemd/system/platform-containers.service ]; "
            "then systemctl stop platform-containers.service; fi",
        ],
        _if=lambda: any(
            host.get_fact(File, path=path) for path in container_metric_paths
        ),
    )
    container_metric_files = [
        files.file(
            name=f"Remove obsolete {Path(path).name}",
            path=path,
            present=False,
        )
        for path in container_metric_paths
    ]
    systemd.daemon_reload(
        name="Reload systemd after container metrics cleanup",
        _if=any_changed(*container_metric_files),
    )

if monitoring_enabled:

    mesh_hostname = host.data.mesh_hostname
    textfile = f"{platform_root}/data/node-exporter/textfile"

    nodes = [
        {
            "name": name,
            "target": (
                "lab-node-exporter:9100"
                if name == hub
                else f"{hosts[name]['mesh_hostname']}:9100"
            ),
        }
        for name in servers
    ]

    _dirs((textfile, "755"))

    files.put(
        name="Install platform-containers",
        src=str(
            core_root
            / "pyinfra"
            / "files"
            / "monitor"
            / "platform-containers"
        ),
        dest="/usr/local/bin/platform-containers",
        user="root",
        group="root",
        mode="755",
    )

    files.put(
        name="Install platform-containers service",
        src=io.StringIO(
            "[Unit]\n"
            "Description=Write container state for node_exporter\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "UMask=0022\n"
            "ExecStart=/usr/local/bin/platform-containers\n"
        ),
        dest="/etc/systemd/system/platform-containers.service",
        user="root",
        group="root",
        mode="644",
    )

    files.put(
        name="Install platform-containers timer",
        src=io.StringIO(
            "[Unit]\n"
            "Description=Run platform-containers every 30s\n"
            "\n"
            "[Timer]\n"
            "OnBootSec=30s\n"
            "OnUnitActiveSec=30s\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
        dest="/etc/systemd/system/platform-containers.timer",
        user="root",
        group="root",
        mode="644",
    )

    systemd.service(
        name="Enable platform-containers timer",
        service="platform-containers.timer",
        running=True,
        enabled=True,
        daemon_reload=True,
    )

    server.shell(
        name="Export container state",
        commands=["/usr/local/bin/platform-containers"],
    )

    if is_hub or is_probe:
        server.shell(
            name="Create metrics network",
            commands=[
                "docker network inspect metrics >/dev/null 2>&1 "
                "|| docker network create metrics >/dev/null"
            ],
        )

    _put_compose(
        "node-exporter",
        join_metrics=is_hub,
        publish=not is_hub,
    )
    _up("node-exporter")

    if is_probe:
        stack = f"{platform_root}/stacks/blackbox-exporter"
        secrets_dir = f"{platform_root}/secrets/monitoring"
        external_url = (
            f"https://{host.data.monitor_ui}" if host.data.monitor_ui else ""
        )
        monitor_mesh_hostname = hosts[hub]["mesh_hostname"]

        _put_compose("blackbox-exporter")
        _dirs((secrets_dir, "700"))

        blackbox_cfg = files.put(
            name="Install Blackbox Exporter config",
            src=str(
                core_root
                / "stacks"
                / "labs"
                / "blackbox-exporter"
                / "blackbox.yml"
            ),
            dest=f"{stack}/blackbox.yml",
            user="root",
            group="root",
            mode="644",
        )

        _up("blackbox-exporter")

        project_dir = shlex.quote(stack)
        server.shell(
            name="Restart Blackbox Exporter",
            commands=[
                f"docker compose --project-directory {project_dir} "
                "restart blackbox"
            ],
            _if=blackbox_cfg.did_change,
        )

        healthchecks_ping_key = host.data.healthchecks_ping_key
        if healthchecks_ping_key:
            healthchecks_url = (
                f"https://hc-ping.com/{healthchecks_ping_key}/monitoring"
                "?create=1"
            )
            files.put(
                name="Write monitoring heartbeat environment",
                src=io.StringIO(
                    "HEALTHCHECKS_URL="
                    f"{shlex.quote(healthchecks_url)}\n"
                    "MONITOR_PROMETHEUS_URL="
                    f"{shlex.quote(f'http://{monitor_mesh_hostname}:9090/-/ready')}\n"
                    "MONITOR_ALERTMANAGER_URL="
                    f"{shlex.quote(f'http://{monitor_mesh_hostname}:9093/-/ready')}\n"
                    f"MONITOR_EXTERNAL_URL={shlex.quote(external_url)}\n"
                ),
                dest=f"{secrets_dir}/healthchecks.env",
                user="root",
                group="root",
                mode="600",
            )

            files.put(
                name="Install monitoring heartbeat",
                src=str(
                    core_root
                    / "pyinfra"
                    / "files"
                    / "monitor"
                    / "platform-monitor-heartbeat"
                ),
                dest="/usr/local/bin/platform-monitor-heartbeat",
                user="root",
                group="root",
                mode="755",
            )

            files.put(
                name="Install monitoring heartbeat service",
                src=io.StringIO(
                    "[Unit]\n"
                    "Description=Verify monitoring and ping Healthchecks.io\n"
                    "After=docker.service network-online.target\n"
                    "Wants=network-online.target\n"
                    "\n"
                    "[Service]\n"
                    "Type=oneshot\n"
                    "ExecStart=/usr/local/bin/platform-monitor-heartbeat\n"
                ),
                dest="/etc/systemd/system/platform-monitor-heartbeat.service",
                user="root",
                group="root",
                mode="644",
            )

            files.put(
                name="Install monitoring heartbeat timer",
                src=io.StringIO(
                    "[Unit]\n"
                    "Description=Verify monitoring every 5 minutes\n"
                    "\n"
                    "[Timer]\n"
                    "OnActiveSec=2m\n"
                    "OnUnitActiveSec=5m\n"
                    "AccuracySec=30s\n"
                    "Persistent=true\n"
                    "\n"
                    "[Install]\n"
                    "WantedBy=timers.target\n"
                ),
                dest="/etc/systemd/system/platform-monitor-heartbeat.timer",
                user="root",
                group="root",
                mode="644",
            )

            systemd.service(
                name="Enable monitoring heartbeat timer",
                service="platform-monitor-heartbeat.timer",
                running=True,
                enabled=True,
                daemon_reload=True,
            )

    if is_hub:
        stack = f"{platform_root}/stacks/prometheus"
        secrets_dir = f"{platform_root}/secrets/prometheus"
        external_url = ""
        caddy_site = None

        admin_user = host.data.admin_user
        prometheus_fqdn = host.data.monitor_ui
        acme_zone = host.data.acme_zone

        if prometheus_fqdn:
            if not acme_zone:
                raise ValueError("infra.yaml: dns.acme_zone required for monitor_ui")
            external_url = f"https://{prometheus_fqdn}"
            caddy_site = files.template(
                name="Install Prometheus Caddy site",
                src=str(
                    core_root
                    / "stacks"
                    / "labs"
                    / "prometheus"
                    / "prometheus.caddy.j2"
                ),
                dest=(
                    f"{platform_root}/stacks/caddy/config/sites/"
                    "prometheus.caddy"
                ),
                user=admin_user,
                group=admin_user,
                mode="644",
                prometheus_fqdn=prometheus_fqdn,
                acme_override=f"{hostname}.{acme_zone}",
            )

        _put_compose(
            "prometheus",
            external_url=external_url,
        )
        _dirs((secrets_dir, "700"))

        files.file(
            name="Remove legacy local Blackbox config",
            path=f"{stack}/blackbox.yml",
            present=False,
        )

        prometheus_data = f"{platform_root}/data/prometheus"
        alertmanager_data = f"{platform_root}/data/alertmanager"

        for path in (prometheus_data, alertmanager_data):
            files.directory(
                name=f"Create {path}",
                path=path,
                user="65534",
                group="65534",
                mode="755",
            )

        server.shell(
            name="Own Prometheus data",
            commands=[
                "chown -R 65534:65534 "
                f"{shlex.quote(prometheus_data)} "
                f"{shlex.quote(alertmanager_data)}"
            ],
        )

        rules = files.put(
            name="Install Prometheus rules",
            src=str(
                core_root
                / "stacks"
                / "labs"
                / "prometheus"
                / "rules.yml"
            ),
            dest=f"{stack}/rules.yml",
            user="root",
            group="root",
            mode="644",
        )

        probes = [
            {
                "service": "prometheus",
                "module": "http_2xx",
                "target_yaml": json.dumps(f"http://{mesh_hostname}:9090/-/ready"),
            },
            {
                "service": "alertmanager",
                "module": "http_2xx",
                "target_yaml": json.dumps(f"http://{mesh_hostname}:9093/-/ready"),
            },
        ]
        if external_url:
            probes.append(
                {
                    "service": "prometheus-external",
                    "module": "http_2xx",
                    "target_yaml": json.dumps(f"{external_url}/-/ready"),
                }
            )
        probes.extend(
            {
                "service": service,
                "module": probe["module"],
                "target_yaml": json.dumps(probe["target"]),
            }
            for service, probe in sorted(host.data.monitor_probes.items())
        )

        prom_cfg = files.template(
            name="Render Prometheus config",
            src=str(
                core_root
                / "stacks"
                / "labs"
                / "prometheus"
                / "prometheus.yml.j2"
            ),
            dest=f"{stack}/prometheus.yml",
            user="root",
            group="root",
            mode="644",
            nodes=nodes,
            probes=probes,
            monitor_host=hostname,
            probe_host=probe_host,
            blackbox_address=f"{hosts[probe_host]['mesh_hostname']}:9115",
        )

        telegram_monitoring_thread_id = host.data.telegram_threads.get(
            "monitoring", ""
        )

        am_cfg = files.template(
            name="Render Alertmanager config",
            src=str(
                core_root
                / "stacks"
                / "labs"
                / "prometheus"
                / "alertmanager.yml.j2"
            ),
            dest=f"{secrets_dir}/alertmanager.yml",
            user="65534",
            group="65534",
            mode="400",
            telegram_enabled=host.data.telegram_alerting_enabled,
            telegram_token_yaml=json.dumps(
                host.data.telegram_alerter_bot_token
            ),
            telegram_chat_id=host.data.telegram_chat_id,
            telegram_monitoring_thread_id=(
                int(telegram_monitoring_thread_id)
                if telegram_monitoring_thread_id
                else None
            ),
        )

        am_tmpl = files.put(
            name="Install Alertmanager Telegram template",
            src=str(
                core_root
                / "stacks"
                / "labs"
                / "prometheus"
                / "telegram.tmpl"
            ),
            dest=f"{stack}/telegram.tmpl",
            user="root",
            group="root",
            mode="644",
        )

        _up("prometheus")

        project_dir = shlex.quote(stack)

        server.shell(
            name="Restart Prometheus",
            commands=[
                f"docker compose --project-directory {project_dir} "
                "restart prometheus"
            ],
            _if=lambda: rules.did_change() or prom_cfg.did_change(),
        )

        server.shell(
            name="Restart Alertmanager",
            commands=[
                f"docker compose --project-directory {project_dir} "
                "restart alertmanager"
            ],
            _if=lambda: am_cfg.did_change() or am_tmpl.did_change(),
        )

        if caddy_site is not None:
            server.shell(
                name="Validate and reload Caddy for Prometheus",
                commands=[
                    "docker exec caddy caddy validate "
                    "--config /etc/caddy/Caddyfile --adapter caddyfile "
                    "&& docker exec caddy caddy reload "
                    "--config /etc/caddy/Caddyfile --adapter caddyfile"
                ],
                _if=caddy_site.did_change,
            )
