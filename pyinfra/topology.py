from __future__ import annotations

import shutil
import subprocess
import sys
from itertools import combinations

from firewall import host_ports
from hosts import (
    customer_dns_records,
    customer_hosts,
    customer_yaml_path,
    load_customer_cfg,
)
from tunnel import hostnames

DASHED = "{style.stroke-dash: 3}"
CARD = '{shape: rectangle; style.fill: "#F7F8FE"}'
FIELDS = {"role": "role", "provider": "provider", "public": "public_ip"}

HOST = "host"
CADDY = "caddy"
CLOUDFLARED = "cloudflared"
NODE_EXPORTER = "node_exporter"
PROMETHEUS = "prometheus"
ALERTMANAGER = "alertmanager"
BLACKBOX_EXPORTER = "blackbox_exporter"
HEARTBEAT = "heartbeat"


def _id(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name)


def _block(eid: str, items: list[str]) -> list[str]:
    lines = (line for item in items for line in item.split("\n"))
    return [f"{eid}: {{", *(f"  {line}" if line else "" for line in lines), "}"]


def _fqdns(records: dict, name: str) -> list[str]:
    return [
        block["zone"] if sub == "apex" else f"{sub}.{block['zone']}"
        for block in records["zones"].values()
        for sub, rec in (block.get("records") or {}).items()
        if rec.get("host") == name
    ]


def _ports(meta: dict, scope: str) -> dict[str, dict]:
    return host_ports(meta["ports"], scope, (meta["role"],), meta)


def _port_rows(label: str, ports: dict[str, dict]) -> list[tuple[str, str]]:
    return [(label, f"{key} {spec['port']}/{spec['proto']}") for key, spec in ports.items()]


def _card(
    name: str,
    meta: dict,
    fields: tuple[str, ...],
    links: list[str] = (),
    rows: list[tuple[str, str]] = (),
    eid: str | None = None,
) -> str:
    values = [*((f, meta.get(FIELDS[f])) for f in fields), *rows]
    groups = [
        " ".join(filter(None, ("###", meta.get("icon"), name))),
        "\\\n".join(f"`{f}` {v}" for f, v in values if v),
        "\\\n".join(f"[{d}](https://{d})" for d in links),
    ]
    eid = eid or _id(name)
    return "\n".join([f"{eid}: |md", "\n\n".join(filter(None, groups)), "|", f"{eid}: {CARD}"])


def _service_card(eid: str, title: str, rows: list[str] = ()) -> str:
    body = "\n\n".join([f"### {title}", "\\\n".join(f"`{row}`" for row in rows)])
    return "\n".join([f"{eid}: |md", body, "|", f"{eid}: {CARD}"])


def _host_group(name: str, meta: dict, items: list[str]) -> list[str]:
    eid = _id(name)
    title = " ".join(filter(None, (meta.get("icon"), name)))
    return _block(
        eid,
        [
            f'label: "{title}"',
            'style.fill: "#E3E9FD"',
            "direction: down",
            *items,
        ],
    )


def render_hosts(hosts: dict, records: dict) -> str:
    servers = sorted(hosts)

    items = ["direction: right"]
    cards = [
        _card(
            n,
            hosts[n],
            ("role", "provider", "public"),
            _fqdns(records, n),
        )
        for n in servers
    ]
    items.append("\n".join(_block(
        "servers",
        ['label: ""', "style.fill: transparent", "style.stroke: transparent", "direction: right", *cards],
    )))
    items += [
        f"servers.{_id(a)} <-> servers.{_id(b)}: WireGuard P2P {DASHED}"
        for a, b in combinations(servers, 2)
        if not hosts[a].get("isolated") and not hosts[b].get("isolated")
    ]
    return "\n".join([
        "direction: down",
        "",
        'cloud: "NetBird Cloud" {shape: cloud}',
        "",
        *_block("mesh", items),
        "",
        f"cloud <-> mesh: control / discovery / relay fallback {DASHED}",
        "",
    ])


def render_ingress(hosts: dict, records: dict) -> str:
    groups, edges = [], []
    for n in sorted(hosts):
        m = hosts[n]
        public = _ports(m, "public")
        tunneled = hostnames(records, n)
        host_rows = [
            f"provider {m.get('provider', '')}",
            f"public {m['public_ip']}",
            *(f"direct {key} {spec['port']}/{spec['proto']}" for key, spec in public.items()),
        ]
        items = [
            _service_card(HOST, "Host", host_rows),
            _service_card(
                CADDY,
                "Caddy",
                [f"netbird :{m['http_port']} / :{m['https_port']}"],
            ),
        ]
        if tunneled:
            items.append(
                _service_card(CLOUDFLARED, "Cloudflared", ["outbound tunnel", *tunneled])
            )
        if public:
            edges.append(f"internet -> {_id(n)}.{HOST}: {', '.join(public)}")
        if tunneled:
            edges += [
                f"cloudflare -> {_id(n)}.{CLOUDFLARED}: tunnel {DASHED}",
                f"{_id(n)}.{CLOUDFLARED} -> {_id(n)}.{CADDY}: proxy network",
            ]
        groups.append(
            "\n".join(
                _block(
                    _id(n),
                    [
                        f'label: "{" ".join(filter(None, (m.get("icon"), n)))}"',
                        'style.fill: "#E3E9FD"',
                        "direction: down",
                        *items,
                    ],
                )
            )
        )
    head = ["internet: {shape: cloud}"]
    if any(e.startswith("cloudflare") for e in edges):
        head += ["cloudflare: {shape: cloud}", "internet -> cloudflare"]
    return "\n".join(["direction: right", "", *head, "", *groups, "", *edges, ""])


def render_monitoring(hosts: dict, cfg: dict) -> str:
    monitor = str(cfg.get("monitor_host") or "").strip()
    probe = str(cfg.get("monitor_probe_host") or "").strip()
    if not monitor or not probe:
        return "monitoring: Monitoring is disabled\n"

    servers = sorted(hosts)
    groups: list[str] = []
    for name in servers:
        items = [
            _service_card(
                NODE_EXPORTER,
                "Node Exporter",
                [f"netbird :{hosts[name][f'{NODE_EXPORTER}_port']}"],
            )
        ]
        if name == monitor:
            items += [
                _service_card(
                    PROMETHEUS,
                    "Prometheus",
                    [f"netbird :{hosts[name][f'{PROMETHEUS}_port']}"],
                ),
                _service_card(
                    ALERTMANAGER,
                    "Alertmanager",
                    [f"netbird :{hosts[name][f'{ALERTMANAGER}_port']}"],
                ),
            ]
        if name == probe:
            items += [
                _service_card(
                    BLACKBOX_EXPORTER,
                    "Blackbox Exporter",
                    [f"netbird :{hosts[name][f'{BLACKBOX_EXPORTER}_port']}"],
                ),
                _service_card(HEARTBEAT, "Monitoring heartbeat", ["every 5 min"]),
            ]
        groups.append("\n".join(_host_group(name, hosts[name], items)))

    monitor_id, probe_id = _id(monitor), _id(probe)
    monitor_ref, probe_ref = f"hosts.{monitor_id}", f"hosts.{probe_id}"
    blackbox_port = hosts[probe][f"{BLACKBOX_EXPORTER}_port"]
    edges = [
        f"hosts.{_id(name)}.{NODE_EXPORTER} -> "
        f"{monitor_ref}.{PROMETHEUS}: "
        f"scrape :{hosts[name][f'{NODE_EXPORTER}_port']}"
        for name in servers
    ]
    edges += [
        f"{monitor_ref}.{PROMETHEUS} -> {probe_ref}.{BLACKBOX_EXPORTER}: "
        f"probe :{blackbox_port} {DASHED}",
        f"{probe_ref}.{BLACKBOX_EXPORTER} -> {monitor_ref}.{PROMETHEUS}: "
        f"readiness {DASHED}",
        f"{probe_ref}.{BLACKBOX_EXPORTER} -> {monitor_ref}.{ALERTMANAGER}: "
        f"readiness {DASHED}",
        f"{monitor_ref}.{PROMETHEUS} -> {monitor_ref}.{ALERTMANAGER}: alerts",
        f"{probe_ref}.{HEARTBEAT} -> {probe_ref}.{BLACKBOX_EXPORTER}: "
        f"readiness {DASHED}",
    ]

    extras = []
    if monitor_ui := str(cfg.get("monitor_ui") or "").strip():
        extras.append(_service_card("private_ui", "Private users", [monitor_ui]))
        edges.append(f"private_ui -> {monitor_ref}.{PROMETHEUS}: HTTPS via Caddy")

    probes = cfg.get("monitor_probes") or {}
    if probes:
        targets = []
        for service, spec in sorted(probes.items()):
            target = spec.get("target", spec) if isinstance(spec, dict) else spec
            targets.append(f"{service} → {target}")
        extras.append(_service_card("probe_targets", "Configured service probes", targets))
        edges.append(f"{probe_ref}.{BLACKBOX_EXPORTER} -> probe_targets: probes")

    telegram = cfg.get("telegram") or {}
    if telegram.get("chat_id"):
        extras.append(_service_card("telegram", "Telegram", ["Monitoring topic"]))
        edges.append(f"{monitor_ref}.{ALERTMANAGER} -> telegram: alerts / resolved")

    extras.append(_service_card("healthchecks", "Healthchecks.io", ["dead-man switch"]))
    edges.append(f"{probe_ref}.{HEARTBEAT} -> healthchecks: success ping {DASHED}")
    if telegram.get("chat_id"):
        edges.append(
            "healthchecks -> telegram: failures / recoveries "
            f"{DASHED}"
        )

    host_groups = _block(
        "hosts",
        [
            'label: ""',
            "style.fill: transparent",
            "style.stroke: transparent",
            "direction: right",
            *groups,
        ],
    )
    placement = [
        "private_ui.near: top-center",
        "telegram.near: bottom-center",
    ]
    return "\n".join(
        ["direction: down", "", *host_groups, "", *extras, "", *edges, "", *placement, ""]
    )


def write_d2() -> None:
    customer_dir = customer_yaml_path().parent
    out = customer_dir / "topology"
    hosts, records = customer_hosts(), customer_dns_records()
    diagrams = {
        "hosts": render_hosts(hosts, records),
        "ingress": render_ingress(hosts, records),
        "monitoring": render_monitoring(hosts, load_customer_cfg()),
    }
    d2 = shutil.which("d2")
    if not d2:
        print("skip svg: mise install  # d2 in mise.toml", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    for name, body in diagrams.items():
        src = out / f"{name}.d2"
        src.write_text(body)
        print(f"wrote {src.relative_to(customer_dir)}")
        if d2:
            svg = src.with_suffix(".svg")
            subprocess.run([d2, str(src), str(svg)], check=True)
            print(f"wrote {svg.relative_to(customer_dir)}")


if __name__ == "__main__":
    write_d2()
