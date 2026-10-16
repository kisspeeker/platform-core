"""UFW rules from infra ports `scope` (public | mesh)."""

from __future__ import annotations

from collections.abc import Container, Mapping

PROTOS = ("tcp", "udp")


def host_ports(ports: Mapping, scope: str, groups: Container, data: Mapping) -> dict[str, dict]:
    """Ports of `scope` enabled on a host: `when` matches a group (role) or a truthy host flag."""
    out = {}
    for key, spec in ports.items():
        when = spec.get("when")
        if spec.get("scope", "public") != scope or (when and when not in groups and not data.get(when)):
            continue
        if spec.get("proto") not in PROTOS or not isinstance(spec.get("port"), int):
            raise ValueError(f"ports.{key}: need int port and proto {'|'.join(PROTOS)}")
        out[key] = spec
    return out


def ufw_rules(host, mesh_cidr: str) -> list[str]:
    """Same format as `ufw show added`."""
    def ports(scope: str) -> list[dict]:
        return list(host_ports(host.data.ports, scope, host.groups, host.data).values())

    return [
        *[f"ufw allow {p['port']}/{p['proto']}" for p in ports("public")],
        *[f"ufw allow from {mesh_cidr} to any port {p['port']} proto {p['proto']}" for p in ports("mesh")],
    ]
