import os
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CORE = "core"
NODE = "node"
MESH = (CORE, NODE)


def load_yaml(text: str) -> dict:
    return yaml.safe_load(text) or {}


def customer_yaml_path() -> Path:
    raw = os.getenv("CUSTOMER_YAML", "").strip()
    if not raw:
        raise SystemExit("Set CUSTOMER_YAML=path/to/infra.yaml")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    else:
        path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"CUSTOMER_YAML not found: {path}")
    return path


@cache
def load_customer_cfg() -> dict:
    return load_yaml(customer_yaml_path().read_text())


def customer_dns_records(cfg: dict | None = None) -> dict:
    """Mesh/public DNS: infra.records or sibling dns/records.yaml."""
    cfg = cfg if cfg is not None else load_customer_cfg()
    if cfg.get("records"):
        return cfg["records"]
    path = customer_yaml_path().parent / "dns" / "records.yaml"
    if path.is_file():
        return load_yaml(path.read_text())
    return {}


def customer_hosts(cfg: dict | None = None) -> dict:
    """Load server peers and their roles/ports."""
    from group_data.all import default_ports

    cfg = cfg if cfg is not None else load_customer_cfg()
    monitor = str(cfg.get("monitor_host") or "").strip()
    monitor_probe = str(cfg.get("monitor_probe_host") or "").strip()
    monitoring_enabled = bool(monitor and monitor_probe)
    if bool(monitor) != bool(monitor_probe):
        raise ValueError(
            "infra.yaml: monitor_host and monitor_probe_host must be set together"
        )
    if monitoring_enabled and monitor_probe == monitor:
        raise ValueError("infra.yaml: monitor_probe_host must differ from monitor_host")

    base_ports = {**default_ports, **(cfg.get("ports") or {})}
    out = {}
    for name, meta in (cfg.get("servers") or {}).items():
        ip = str(meta.get("public_ip", "")).strip()
        if not ip:
            raise ValueError(f"infra.yaml servers.{name}.public_ip required")
        role = str(meta.get("role") or NODE).strip()
        if role not in MESH:
            raise ValueError(
                f"infra.yaml servers.{name}.role must be "
                f"{'|'.join(MESH)}"
            )
        ports = {**base_ports, **(meta.get("ports") or {})}
        if role != CORE:
            ports = {
                k: v
                for k, v in ports.items()
                if v.get("when") != CORE
            }
        data = {
            **{k: v for k, v in meta.items() if k != "ports"},
            "role": role,
            "public_ip": ip,
            "mesh_hostname": name,
            "ports": ports,
            "monitoring_enabled": monitoring_enabled,
            "is_monitor_host": name == monitor,
            "is_monitor_probe_host": name == monitor_probe,
        }
        for key, spec in ports.items():
            data[f"{key}_port"] = spec["port"]
        out[name] = data

    if monitoring_enabled and (
        monitor not in out or out[monitor]["role"] not in MESH
    ):
        raise ValueError(
            f"infra.yaml monitor_host must be a core|node, got {monitor!r}"
        )
    if monitoring_enabled and (
        monitor_probe not in out
        or out[monitor_probe]["role"] not in MESH
    ):
        raise ValueError(
            "infra.yaml monitor_probe_host must be a core|node, "
            f"got {monitor_probe!r}"
        )
    if not any(m["role"] == CORE for m in out.values()):
        raise ValueError("infra.yaml: need at least one servers.*.role: core")
    return out

