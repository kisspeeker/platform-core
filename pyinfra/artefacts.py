from datetime import datetime, timezone

from pyinfra import host
from pyinfra.operations import python

import mesh
import sops_env
from hosts import customer_hosts

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
        if mesh.CORE in remote.groups or mesh.NODE in remote.groups
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
            "mesh_ip": remote.data.mesh_ip,
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

    all_hosts = customer_hosts()
    clients = mesh.write_clients(all_hosts, sops_env.AWG_CLIENTS_DIR)

    artefacts = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hosts": hosts,
        "restic": {"password": _read(host, "/etc/platform-restic.password")},
        "awg": {
            "mesh_cidr": host.data.mesh_cidr,
            "dns": mesh.core_ip(all_hosts),
            "clients": clients,
        },
    }

    sops_env.set_secrets(
        RESTIC_PASSWORD=artefacts["restic"]["password"],
    )
    sops_env.write_artefacts(artefacts)
    for path in clients.values():
        print(f"Wrote {path} (+ .png QR)")

# Runs on all hosts (nodes must be connected to read their secrets); core writes the dump.
if mesh.CORE in host.groups:
    python.call(
        name="Dump platform artefacts",
        function=dump_artefacts,
    )
