# Docs: https://docs.amnezia.org/documentation/amnezia-wg/
import io

from pyinfra import host, local
from pyinfra.facts.files import File, FindFiles
from pyinfra.operations import apt, files, server, systemd

import mesh

hosts = host.data.mesh_hosts
name = host.data.hostname

awg_go_version = "v3.1.20260828"
awg_go_dir = f"/usr/local/lib/amneziawg-go/{awg_go_version}"

apt.packages(
    name="Install AmneziaWG prerequisites",
    packages=["software-properties-common"],
    update=True,
    cache_time=3600,
)

if not host.get_fact(FindFiles, path="/etc/apt/sources.list.d", fname="amnezia-ubuntu-ppa-*"):
    apt.ppa(
        name="Add AmneziaWG PPA",
        src="ppa:amnezia/ppa",
    )

apt.packages(
    name="Install AmneziaWG tools",
    packages=["amneziawg-tools"],
    no_recommends=True,
)

# Userspace fallback when the DKMS module does not build for the kernel.
if not host.get_fact(File, path=f"{awg_go_dir}/amneziawg-go"):
    server.shell(
        name=f"Build amneziawg-go {awg_go_version}",
        commands=[
            f"docker run --rm -e CGO_ENABLED=0 -e GOBIN=/out -v {awg_go_dir}:/out "
            f"golang:1.25 go install github.com/amnezia-vpn/amneziawg-go/v3@{awg_go_version}"
        ],
    )

files.link(
    name="Link amneziawg-go",
    path="/usr/local/bin/amneziawg-go",
    target=f"{awg_go_dir}/amneziawg-go",
)

files.directory(
    name="Create AmneziaWG config dir",
    path="/etc/amnezia/amneziawg",
    user="root",
    group="root",
    mode="700",
)

awg_conf = files.put(
    name="Write AmneziaWG config",
    src=io.StringIO(mesh.render(name, hosts)),
    dest=f"/etc/amnezia/amneziawg/{mesh.IFACE}.conf",
    user="root",
    group="root",
    mode="600",
)

systemd.service(
    name="Enable AmneziaWG",
    service=f"awg-quick@{mesh.IFACE}",
    running=True,
    enabled=True,
)

systemd.service(
    name="Reload AmneziaWG",
    service=f"awg-quick@{mesh.IFACE}",
    reloaded=True,
    _if=awg_conf.did_change,
)

docker_dropin = files.put(
    name="Start Docker after AmneziaWG",
    src=io.StringIO(
        "[Unit]\n"
        f"After=awg-quick@{mesh.IFACE}.service\n"
        f"Wants=awg-quick@{mesh.IFACE}.service\n"
    ),
    dest="/etc/systemd/system/docker.service.d/awg.conf",
    user="root",
    group="root",
    mode="644",
    create_remote_dir=True,
)

systemd.daemon_reload(
    name="Reload systemd for Docker drop-in",
    _if=docker_dropin.did_change,
)

local.include("tasks/mesh_dns.py")
