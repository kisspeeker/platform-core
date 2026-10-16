# Docs: https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw

import io
from pathlib import Path

from pyinfra import host
from pyinfra.operations import files, systemd

from firewall import host_ports

CORE_ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = host.data.hostname
FILTER_SCRIPT = "/usr/local/sbin/platform-docker-fw"
FILTER_CONFIG = "/etc/platform-docker-fw"
FILTER_SERVICE = "platform-docker-fw.service"

hub = host.data.monitor_host
probe = host.data.monitor_probe_host

netbird_ingress_ports = host_ports(
    host.data.ports,
    "netbird",
    host.groups,
    host.data,
).values()

netbird_egress_ports = []
if hub and HOSTNAME == hub:
    netbird_egress_ports.extend(("9100", "9115"))
if probe and HOSTNAME == probe:
    netbird_egress_ports.extend(("443", "9090", "9093"))

files.put(
    name="Install Docker NetBird filter",
    src=str(CORE_ROOT / "files" / "firewall" / "platform-docker-fw"),
    dest=FILTER_SCRIPT,
    user="root",
    group="root",
    mode="755",
)

files.put(
    name="Write Docker NetBird filter config",
    src=io.StringIO(
        "netbird_ingress_ports="
        + repr(" ".join(
            f"{spec['proto']}:{spec['port']}"
            for spec in netbird_ingress_ports
        ))
        + "\n"
        f"netbird_egress_ports={' '.join(netbird_egress_ports)!r}\n"
    ),
    dest=FILTER_CONFIG,
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Install Docker NetBird filter service",
    src=io.StringIO(
        "[Unit]\n"
        "Description=Keep container traffic off NetBird\n"
        "After=docker.service netbird.service\n"
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
    name="Enable Docker NetBird filter",
    service=FILTER_SERVICE,
    running=True,
    enabled=True,
    daemon_reload=True,
    restarted=True,
)
