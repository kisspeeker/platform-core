import base64
import binascii
import io
import re
import shlex
from pathlib import Path

from pyinfra import host
from pyinfra.operations import files, server


platform_root = host.data.platform_root
deploy_user = host.data.deploy_user
platform_git_root = Path(__file__).resolve().parents[1]

deploy_script = "/usr/local/bin/platform-deploy"

hostname = host.data.hostname
public_key = host.data.deploy_public_key
age_key = host.data.deploy_sops_age_key

if not public_key:
    raise ValueError("Set DEPLOY_PUBLIC_KEY in secrets.sops.env")

if not age_key:
    raise ValueError("Set DEPLOY_SOPS_AGE_KEY in secrets.sops.env")

if "\n" in public_key or "\r" in public_key:
    raise ValueError("DEPLOY_PUBLIC_KEY must contain exactly one SSH public key")

public_key_fields = public_key.split()
if len(public_key_fields) < 2:
    raise ValueError("DEPLOY_PUBLIC_KEY must be an SSH public key")

key_type, key_data = public_key_fields[:2]
if not re.fullmatch(r"[A-Za-z0-9@._+-]+", key_type):
    raise ValueError("DEPLOY_PUBLIC_KEY has an invalid key type")

try:
    decoded_key = base64.b64decode(key_data, validate=True)
except (ValueError, binascii.Error) as exc:
    raise ValueError("DEPLOY_PUBLIC_KEY has invalid base64 key data") from exc

if not decoded_key:
    raise ValueError("DEPLOY_PUBLIC_KEY has empty key data")

public_key = " ".join(public_key_fields)

if "\n" in age_key or "\r" in age_key or any(char.isspace() for char in age_key):
    raise ValueError("DEPLOY_SOPS_AGE_KEY must contain exactly one AGE key")

if not age_key.startswith("AGE-SECRET-KEY-"):
    raise ValueError("DEPLOY_SOPS_AGE_KEY must start with AGE-SECRET-KEY-")

gitlab_group = host.data.gitlab_group
acme_zone = host.data.acme_zone


def env_assignment(name, value):
    value = "" if value is None else str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must not contain control characters")
    return f"{name}={shlex.quote(value)}\n"


host_env = "".join(
    (
        env_assignment("PLATFORM_ROOT", platform_root),
        env_assignment("ADMIN_USER", host.data.admin_user),
        env_assignment("DEPLOY_GROUP", gitlab_group),
        env_assignment("INVENTORY_HOSTNAME", hostname),
        env_assignment("MESH_IP", host.data.mesh_ip),
        env_assignment("ACME_ZONE", acme_zone),
        env_assignment("DNS_ZONE", host.data.dns_zone),
        env_assignment("GITLAB_REGISTRY", host.data.gitlab_registry),
    )
)

files.directory(
    name="Create platform root",
    path=platform_root,
    user="root",
    group="root",
    mode="755",
)

server.user(
    name="Create deploy user",
    user=deploy_user,
    groups=["docker"],
    shell="/bin/bash",
    create_home=True,
    append=True,
    password="!",
)

files.put(
    name="Install platform-deploy",
    src=str(platform_git_root / "files" / "deploy" / "platform-deploy"),
    dest=deploy_script,
    user="root",
    group="root",
    mode="755",
)

files.directory(
    name="Create deploy ssh dir",
    path=f"/home/{deploy_user}/.ssh",
    user="root",
    group="root",
    mode="755",
)

files.put(
    name="Install deploy authorized_keys",
    src=io.StringIO(f'command="{deploy_script}",restrict {public_key}\n'),
    dest=f"/home/{deploy_user}/.ssh/authorized_keys",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write host.env",
    src=io.StringIO(host_env),
    dest=f"{platform_root}/host.env",
    user="root",
    group="root",
    mode="644",
)

files.directory(
    name="Create stacks directory",
    path=f"{platform_root}/stacks",
    user=deploy_user,
    group=deploy_user,
    mode="755",
)

files.directory(
    name="Caddy sites writable by deploy",
    path=f"{platform_root}/stacks/caddy/config/sites",
    user=deploy_user,
    group=deploy_user,
    mode="755",
)

files.directory(
    name="Create sops secrets dir",
    path=f"{platform_root}/secrets/sops",
    user="root",
    group=deploy_user,
    mode="750",
)

files.put(
    name="Install host sops age key",
    src=io.StringIO(f"{age_key}\n"),
    dest=f"{platform_root}/secrets/sops/age.key",
    user="root",
    group=deploy_user,
    mode="640",
)
