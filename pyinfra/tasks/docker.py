import io
import json

from pyinfra import host
from pyinfra.facts.server import Arch, LinuxDistribution
from pyinfra.operations import apt, files, server, systemd

admin_user = host.data.admin_user

arch = host.get_fact(Arch)
arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(arch, arch)
distro = host.get_fact(LinuxDistribution) or {}
meta = distro.get("release_meta") or {}
codename = meta.get("VERSION_CODENAME") or meta.get("CODENAME")
if not codename:
    raise ValueError("Unable to detect Ubuntu VERSION_CODENAME for Docker apt repo.")

apt.packages(
    name="Remove Docker packages",
    packages=[
        "docker.io",
        "docker-compose",
        "docker-compose-v2",
        "docker-doc",
        "podman-docker",
        "containerd",
        "runc",
    ],
    present=False,
)

apt.packages(
    name="Install Docker prerequisites",
    packages=["ca-certificates", "curl"],
)

files.directory(
    name="Create APT directory",
    path="/etc/apt/keyrings",
    mode="755",
)

files.download(
    name="Download Docker key",
    src="https://download.docker.com/linux/ubuntu/gpg",
    dest="/etc/apt/keyrings/docker.asc",
    mode="644",
)

docker_repo = apt.repo(
    name="Add Docker repository",
    src=(
        f"deb [arch={arch} signed-by=/etc/apt/keyrings/docker.asc] "
        f"https://download.docker.com/linux/ubuntu {codename} stable"
    ),
    filename="docker",
)

# Docs: https://docs.docker.com/engine/logging/drivers/json-file/
daemon_json = files.put(
    name="Write Docker daemon.json",
    src=io.StringIO(
        json.dumps(
            {
                "log-driver": "json-file",
                "log-opts": {"max-size": "10m", "max-file": "3"},
                "live-restore": True,
            },
            indent=2,
        )
        + "\n"
    ),
    dest="/etc/docker/daemon.json",
    user="root",
    group="root",
    mode="644",
    create_remote_dir=True,
)

server.sysctl(
    name="Allow binding not-yet-assigned IPs",
    key="net.ipv4.ip_nonlocal_bind",
    value=1,
    persist=True,
    persist_file="/etc/sysctl.d/99-platform.conf",
)

apt.update(
    name="Update apt cache for Docker",
    _if=docker_repo.did_change,
)

apt.packages(
    name="Install Docker",
    packages=[
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
    ],
)

server.user(
    name="Add Docker users",
    user=admin_user,
    groups=["docker"],
    append=True,
)

systemd.service(
    name="Start Docker socket",
    service="docker.socket",
    running=True,
    enabled=True,
)

systemd.service(
    name="Start Docker",
    service="docker",
    running=True,
    enabled=True,
)

systemd.service(
    name="Restart Docker",
    service="docker",
    restarted=True,
    _if=daemon_json.did_change,
)
