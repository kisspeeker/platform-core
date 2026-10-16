from pyinfra import host
from pyinfra.operations import apt, files, systemd

import mesh
from hosts import customer_dns_records

hosts = host.data.mesh_hosts
dns = customer_dns_records()
name = host.data.hostname
mesh_ip = host.data.mesh_ip

records = mesh.dns_records(dns, hosts)
mesh_zones = sorted(
    b["zone"] for b in (dns.get("zones") or {}).values() if b.get("backend") == "mesh"
)
host_lines = [
    f"{ip} {fqdn}"
    for fqdn, ip in mesh.visible_records(records, name, hosts)
]

files.block(
    name="Write mesh names to /etc/hosts",
    path="/etc/hosts",
    content="\n".join(host_lines) if host_lines else None,
    present=bool(host_lines),
    marker="# {mark} platform mesh",
)

if mesh.CORE in host.groups:
    files.directory(
        name="Create dnsmasq config dir",
        path="/etc/dnsmasq.d",
        user="root",
        group="root",
        mode="755",
    )

    dnsmasq_conf = files.template(
        name="Write mesh dnsmasq config",
        src="templates/dnsmasq.mesh.conf.j2",
        dest="/etc/dnsmasq.d/platform-mesh.conf",
        user="root",
        group="root",
        mode="644",
        mesh_ip=mesh_ip,
        records=records,
        zones=mesh_zones,
    )

    apt.packages(
        name="Install dnsmasq",
        packages=["dnsmasq"],
    )

    systemd.service(
        name="Enable dnsmasq",
        service="dnsmasq",
        running=True,
        enabled=True,
    )

    systemd.service(
        name="Restart dnsmasq",
        service="dnsmasq",
        restarted=True,
        _if=dnsmasq_conf.did_change,
    )
