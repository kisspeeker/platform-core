from datetime import datetime, timezone

from pyinfra import host
from pyinfra.operations import python

from hosts import CORE, MESH
import sops_env

platform_root = host.data.platform_root
breakglass_user = host.data.breakglass_user

def _read(remote_host, path):
    ok, output = remote_host.run_shell_command(f"cat {path} 2>/dev/null || true", _sudo=True)
    return output.stdout.strip() if ok else ""

def dump_artefacts(state, host):
    hosts = {}
    remotes = [
        remote
        for remote in state.inventory.iter_activated_hosts()
        if set(remote.groups) & set(MESH)
    ]
    if not remotes:
        remotes = [host]

    for remote in remotes:
        name = remote.data.hostname
        otp = _read(
            remote, f"{platform_root}/secrets/breakglass/otp_secret"
        ).upper()
        hosts[name] = {
            "public_ip": remote.data.ssh_hostname,
            "mesh_hostname": remote.data.mesh_hostname,
            "breakglass_user": breakglass_user,
            "breakglass_password": _read(
                remote, f"{platform_root}/secrets/breakglass/password"
            ),
            "breakglass_otp_secret": otp,
            "breakglass_otpauth": (
                f"otpauth://totp/{breakglass_user}@{name}"
                f"?secret={otp}&issuer=platform-breakglass"
            ),
        }

    artefacts = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hosts": hosts,
        "restic": {"password": _read(host, "/etc/platform-restic.password")},
    }

    sops_env.set_secrets(
        RESTIC_PASSWORD=artefacts["restic"]["password"],
    )
    sops_env.write_artefacts(artefacts)

# Runs on all hosts (nodes must be connected to read their secrets); core writes the dump.
if CORE in host.groups:
    python.call(
        name="Dump platform artefacts",
        function=dump_artefacts,
    )
