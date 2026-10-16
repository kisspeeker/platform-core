from __future__ import annotations

from collections.abc import Container, Mapping

PROTOS = ("tcp", "udp")


def host_ports(ports: Mapping, scope: str, groups: Container, data: Mapping) -> dict[str, dict]:
    out = {}
    for key, spec in ports.items():
        when = spec.get("when")
        if spec.get("scope", "public") != scope or (when and when not in groups and not data.get(when)):
            continue
        if spec.get("proto") not in PROTOS or not isinstance(spec.get("port"), int):
            raise ValueError(f"ports.{key}: need int port and proto {'|'.join(PROTOS)}")
        out[key] = spec
    return out


def ufw_rules(host) -> list[str]:
    def ports(scope: str) -> list[dict]:
        return list(host_ports(host.data.ports, scope, host.groups, host.data).values())

    return [
        *[f"ufw allow {p['port']}/{p['proto']}" for p in ports("public")],
        *[f"ufw allow in on wt0 to any port {p['port']} proto {p['proto']}" for p in ports("netbird")],
    ]
