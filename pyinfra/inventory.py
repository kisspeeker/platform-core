from hosts import CORE, customer_hosts, load_customer_cfg

_cfg = load_customer_cfg()
_platform_hosts = customer_hosts(_cfg)

customer = []
core = []
node = []

for _name in _cfg["servers"]:
    _meta = _platform_hosts[_name]
    _row = (
        _name,
        {
            **_meta,
            "ssh_hostname": _meta["public_ip"],
            "ssh_port": int(_meta["ssh_port"]),
            "hostname": _name,
            "customer": True,
            "platform_hosts": _platform_hosts,
        },
    )
    customer.append(_row)
    if _meta["role"] == CORE:
        core.append(_row)
    else:
        node.append(_row)
