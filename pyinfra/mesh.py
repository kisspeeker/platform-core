from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


MESH = {
    "iface": "awg0",
    "core": "core",
    "node": "node",
    "client": "client",
}
IFACE = MESH["iface"]
CORE = MESH["core"]
NODE = MESH["node"]
CLIENT = MESH["client"]


def validate(hosts: dict, cidr: str) -> None:
    net = ipaddress.ip_network(cidr)
    seen: dict[str, str] = {}

    for name, meta in hosts.items():
        raw_ip = str(meta.get("mesh_ip") or "").strip()
        if not raw_ip:
            raise ValueError(f"{name}.mesh_ip required")

        address = ipaddress.ip_address(raw_ip)
        if address not in net:
            raise ValueError(f"{name}.mesh_ip {raw_ip} not in {cidr}")

        normalized_ip = str(address)
        if normalized_ip in seen:
            raise ValueError(
                f"{name}.mesh_ip {raw_ip} duplicates {seen[normalized_ip]}"
            )

        seen[normalized_ip] = name


def _derive(label: str) -> bytes:
    from group_data.all import awg_mesh_secret

    secret = awg_mesh_secret
    try:
        secret_bytes = base64.b64decode(secret, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(
            "AWG_MESH_SECRET must be base64 (openssl rand -base64 32)"
        ) from exc
    if len(secret_bytes) < 32:
        raise RuntimeError(
            "AWG_MESH_SECRET must contain at least 32 random bytes "
            "(openssl rand -base64 32)"
        )

    return hmac.new(
        secret.encode(),
        f"awg:{label}".encode(),
        hashlib.sha256,
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _key(name: str, meta: dict) -> X25519PrivateKey:
    """Derive a per-host key; bump mesh_key_version to rotate one host's key."""
    version = meta.get("mesh_key_version")
    if version is None:
        raise ValueError(f"{name}.mesh_key_version required")
    return X25519PrivateKey.from_private_bytes(
        _derive(f"key:{name}:{version}")
    )


def private_key(meta: dict) -> str:
    return _b64(_key(meta["name"], meta).private_bytes_raw())


def public_key(meta: dict) -> str:
    return _b64(_key(meta["name"], meta).public_key().public_bytes_raw())


def obfuscation() -> dict[str, int]:
    values: list[int] = []
    index = 0

    while len(values) < 4:
        value = 5 + int.from_bytes(_derive(f"h:{index}")[:4]) % (2**31 - 5)
        if value not in values:
            values.append(value)
        index += 1

    return {
        "Jc": 4,
        "Jmin": 40,
        "Jmax": 70,
        "S1": 50,
        "S2": 50,
        "H1": values[0],
        "H2": values[1],
        "H3": values[2],
        "H4": values[3],
    }


def core_ip(hosts: dict) -> str:
    return next(
        (
            meta["mesh_ip"]
            for _, meta in sorted(hosts.items())
            if meta["role"] == CORE
        ),
        "",
    )


def admin_ips(hosts: dict) -> list[str]:
    """Caddy mesh_admin: all servers and clients of the mesh."""
    return sorted(meta["mesh_ip"] for meta in hosts.values())


def linked(a: str, b: str, hosts: dict) -> bool:
    """Whether two hosts are AWG peers under the mesh isolation rules."""
    role_a = hosts[a]["role"]
    role_b = hosts[b]["role"]

    if role_a == CLIENT and role_b == CLIENT:
        return False

    if (
        role_a != CLIENT
        and role_b != CLIENT
        and (hosts[a].get("isolated") or hosts[b].get("isolated"))
    ):
        return False

    return True


def visible_records(
    records: list[tuple[str, str]],
    name: str,
    hosts: dict,
) -> list[tuple[str, str]]:
    """Return DNS records visible to a host without exposing unlinked mesh names."""
    mesh_ips = {meta["mesh_ip"] for meta in hosts.values()}
    reachable_ips = {hosts[name]["mesh_ip"]}
    reachable_ips.update(
        meta["mesh_ip"]
        for peer, meta in hosts.items()
        if peer != name and linked(name, peer, hosts)
    )

    return [
        (fqdn, ip)
        for fqdn, ip in records
        if ip not in mesh_ips or ip in reachable_ips
    ]


def render(name: str, hosts: dict) -> str:
    me = hosts[name]
    prefix = ipaddress.ip_network(me["mesh_cidr"]).prefixlen

    lines = [
        "[Interface]",
        f"Address = {me['mesh_ip']}/{prefix}",
        f"PrivateKey = {_b64(_key(name, me).private_bytes_raw())}",
    ]

    if me["role"] == CLIENT:
        dns = core_ip(hosts)
        if dns:
            lines.append(f"DNS = {dns}")
    else:
        lines.append(f"ListenPort = {me['awg_port']}")

    lines.extend(f"{key} = {value}" for key, value in obfuscation().items())

    for peer, meta in sorted(hosts.items()):
        if peer == name or not linked(name, peer, hosts):
            continue

        lines.extend(
            [
                "",
                "[Peer]",
                f"# {peer}",
                f"PublicKey = {_b64(_key(peer, meta).public_key().public_bytes_raw())}",
                f"AllowedIPs = {meta['mesh_ip']}/32",
            ]
        )

        if meta["role"] != CLIENT:
            lines.extend(
                [
                    f"Endpoint = {meta['public_ip']}:{meta['awg_port']}",
                    "PersistentKeepalive = 25",
                ]
            )

    return "\n".join(lines) + "\n"


def write_clients(hosts: dict, out_dir: str | Path) -> dict[str, str]:
    import segno

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}

    for name, meta in sorted(hosts.items()):
        if meta["role"] != CLIENT:
            continue

        config = render(name, hosts)
        config_path = out_dir / f"{name}.conf"

        config_path.touch(mode=0o600)
        config_path.chmod(0o600)
        config_path.write_text(config, encoding="utf-8")

        qr_path = config_path.with_suffix(".png")
        qr_path.touch(mode=0o600)
        qr_path.chmod(0o600)
        segno.make(config, error="l").save(str(qr_path), scale=8)
        qr_path.chmod(0o600)

        written[name] = str(config_path)

    return written


def dns_records(records: dict, hosts: dict) -> list[tuple[str, str]]:
    out: dict[str, str] = {}

    for block in (records.get("zones") or {}).values():
        if block.get("backend") != "mesh":
            continue

        zone = block["zone"]
        for subdomain, record in (block.get("records") or {}).items():
            fqdn = zone if subdomain == "apex" else f"{subdomain}.{zone}"

            if content := str(record.get("content") or "").strip():
                out[fqdn] = content
            elif record.get("host") in hosts:
                out[fqdn] = hosts[record["host"]]["mesh_ip"]
            else:
                raise ValueError(
                    f"mesh DNS {fqdn}: set content or host from .hosts.yaml"
                )

    return sorted(out.items())
