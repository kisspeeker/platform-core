# Docs: https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw

import io
from pathlib import Path

from pyinfra import host
from pyinfra.operations import files, systemd

CORE_ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = host.data.hostname
FILTER_SCRIPT = "/usr/local/sbin/platform-docker-fw"
FILTER_CONFIG = "/etc/platform-docker-fw"
FILTER_SERVICE = "platform-docker-fw.service"

hosts = host.data.mesh_hosts
hub = host.data.monitor_host
probe = host.data.monitor_probe_host

mesh_egress_ports = []
if hub and HOSTNAME == hub:
    mesh_egress_ports.extend(("9100", "9115"))
if probe and HOSTNAME == probe:
    mesh_egress_ports.extend(("443", "9090", "9093"))

allow_9100_from = (
    hosts[hub]["mesh_ip"] if hub and HOSTNAME != hub else ""
)
allow_monitor_from = (
    hosts[probe]["mesh_ip"] if probe and HOSTNAME == hub else ""
)
allow_blackbox_from = (
    hosts[hub]["mesh_ip"] if hub and HOSTNAME == probe else ""
)

files.put(
    name="Install Docker mesh filter",
    src=str(CORE_ROOT / "files" / "firewall" / "platform-docker-fw"),
    dest=FILTER_SCRIPT,
    user="root",
    group="root",
    mode="755",
)

files.put(
    name="Write Docker mesh filter config",
    src=io.StringIO(
        f"mesh_egress_ports={' '.join(mesh_egress_ports)!r}\n"
        f"allow_9100_from={allow_9100_from!r}\n"
        f"allow_monitor_from={allow_monitor_from!r}\n"
        f"allow_blackbox_from={allow_blackbox_from!r}\n"
    ),
    dest=FILTER_CONFIG,
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Install Docker mesh filter service",
    src=io.StringIO(
        "[Unit]\n"
        "Description=Keep container traffic off the mesh\n"
        "After=docker.service awg-quick@awg0.service\n"
        "PartOf=docker.service\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={FILTER_SCRIPT}\n"
        "RemainAfterExit=yes\n"
        "\n"
        "[Install]\n"
        "WantedBy=docker.service multi-user.target\n"
    ),
    dest=f"/etc/systemd/system/{FILTER_SERVICE}",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable Docker mesh filter",
    service=FILTER_SERVICE,
    running=True,
    enabled=True,
    daemon_reload=True,
    restarted=True,
)
