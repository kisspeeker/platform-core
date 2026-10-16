# Docs: https://github.com/jesseduffield/lazydocker/releases/tag/v0.25.2

import shlex

from pyinfra import host
from pyinfra.facts.server import Arch, Command
from pyinfra.operations import files, server


VERSION = "0.25.2"
CHECKSUMS = {
    "x86_64": "0d9dbfc26068b218e7ed84b104748cadc6e3cf733c0afd35465306fb39b9523c",
    "arm64": "005c38b685aaa557e7d646d83a3dadb5024340eeed8c6a2e1949eee6f530de23",
}
ARCHITECTURES = {
    "x86_64": "x86_64",
    "aarch64": "arm64",
}

system_arch = host.get_fact(Arch)
arch = ARCHITECTURES.get(system_arch)

if arch not in CHECKSUMS:
    raise ValueError(f"Unsupported lazydocker architecture: {system_arch}")

binary = "/usr/local/bin/lazydocker"
cache = f"/var/cache/platform-lazydocker/{VERSION}-{arch}"
archive_name = f"lazydocker_{VERSION}_Linux_{arch}.tar.gz"
tarball = f"{cache}/{archive_name}"
url = (
    "https://github.com/jesseduffield/lazydocker/releases/download/"
    f"v{VERSION}/{archive_name}"
)

files.directory(
    name="Create lazydocker download cache",
    path=cache,
    user="root",
    group="root",
    mode="755",
)

files.download(
    name="Download lazydocker",
    src=url,
    dest=tarball,
    mode="644",
    sha256sum=CHECKSUMS[arch],
)

installed = (
    host.get_fact(
        Command,
        command=f"{shlex.quote(binary)} --version 2>/dev/null || true",
    )
    or ""
)

if VERSION not in installed:
    quoted_cache = shlex.quote(cache)
    quoted_tarball = shlex.quote(tarball)
    quoted_binary = shlex.quote(binary)
    extracted_binary = shlex.quote(f"{cache}/lazydocker")

    server.shell(
        name="Install lazydocker binary",
        commands=[
            f"tar -xzf {quoted_tarball} -C {quoted_cache} && "
            f"install -m 0755 {extracted_binary} {quoted_binary}"
        ],
    )
