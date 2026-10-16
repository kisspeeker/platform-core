import os
from pathlib import Path

import yaml
from pyinfra import host
from pyinfra.api import HiddenValue, QuoteString, StringCommand
from pyinfra.operations import apt, files, server, systemd

config_path = os.environ.get("NETBIRD_CONFIG_FILE", "").strip()
if not config_path:
    raise ValueError("NETBIRD_CONFIG_FILE is required")
config = yaml.safe_load(Path(config_path).read_text()) or {}
setup_keys = config.get("setup_keys") or {}
if not isinstance(setup_keys, dict):
    raise ValueError("netbird.sops.yaml: setup_keys must be a mapping")

setup_key = str(setup_keys.get(host.data.hostname, "")).strip()
registered = host.run_shell_command(
    "netbird status --check ready >/dev/null 2>&1",
    _sudo=True,
)[0]
if not registered and not setup_key:
    raise ValueError(f"netbird.sops.yaml: no setup key for {host.data.hostname}")

files.directory(
    name="Create APT keyring directory",
    path="/etc/apt/keyrings",
    user="root",
    group="root",
    mode="755",
    _sudo=True,
)

apt.key(
    name="NetBird GPG key",
    src="https://pkgs.netbird.io/debian/public.key",
    dest="/etc/apt/keyrings/netbird.gpg",
    _sudo=True,
)

apt.repo(
    name="NetBird repository",
    src="deb [signed-by=/etc/apt/keyrings/netbird.gpg] https://pkgs.netbird.io/debian stable main",
    filename="netbird",
    _sudo=True,
)

apt.packages(
    name="Install NetBird",
    packages=["netbird"],
    update=True,
    _sudo=True,
)

systemd.service(
    name="Enable NetBird",
    service="netbird",
    running=True,
    enabled=True,
    _sudo=True,
)

if not registered:
    server.shell(
        name="Register NetBird peer",
        commands=[
            StringCommand(
                "netbird",
                "up",
                "--setup-key",
                QuoteString(HiddenValue(setup_key)),
                "--hostname",
                QuoteString(host.data.hostname),
            )
        ],
        _sudo=True,
    )
