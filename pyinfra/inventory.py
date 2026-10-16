import mesh
from hosts import customer_hosts, load_customer_cfg

_cfg = load_customer_cfg()
_mesh_hosts = customer_hosts(_cfg)

customer = []
core = []
node = []

for _name in _cfg["servers"]:
    _meta = _mesh_hosts[_name]
    _row = (
        _name,
        {
            **_meta,
            "ssh_hostname": _meta["public_ip"],
            "ssh_port": int(_meta["ssh_port"]),
            "hostname": _name,
            "customer": True,
            "mesh_hosts": _mesh_hosts,
        },
    )
    customer.append(_row)
    if _meta["role"] == mesh.CORE:
        core.append(_row)
    else:
        node.append(_row)
