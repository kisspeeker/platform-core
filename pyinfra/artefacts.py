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
            "hostname": name,
            "role": remote.data.role,
            "public_ip": remote.data.public_ip,
            "netbird_fqdn": f"{name}.peers.{remote.data.dns_zone}",
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

    restic = {
        "primary": {
            "password": _read(host, "/etc/platform-restic.password"),
        },
    }
    offsite_password = _read(host, "/etc/platform-restic-offsite.password")
    if offsite_password:
        restic["offsite"] = {"password": offsite_password}

    artefacts = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hosts": hosts,
        "restic": restic,
    }

    sops_env.set_secrets(
        RESTIC_PRIMARY_PASSWORD=restic["primary"]["password"],
        RESTIC_OFFSITE_PASSWORD=offsite_password,
    )
    sops_env.write_artefacts(artefacts)

# Runs on all hosts (nodes must be connected to read their secrets); core writes the dump.
if CORE in host.groups:
    python.call(
        name="Dump platform artefacts",
        function=dump_artefacts,
    )
