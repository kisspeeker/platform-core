import io
import shlex
from pathlib import Path

from pyinfra import host
from pyinfra.facts.docker import DockerNetwork
from pyinfra.facts.files import File
from pyinfra.operations import docker, files, server
from pyinfra.operations.util import any_changed

import tunnel
from hosts import customer_dns_records

host_name = host.data.hostname
admin_user = host.data.admin_user
platform_root = host.data.platform_root
platform_git_root = Path(__file__).resolve().parents[2]

pull_policy = host.data.bootstrap_pull_policy
acme_email = host.data.acme_email
cf_acme_token = host.data.cf_acme_token
registry_user = host.data.platform_deploy_username
registry_token = host.data.platform_deploy_token

registry_server = host.data.gitlab_registry
acme_zone = host.data.acme_zone

proxy_subnet = "172.31.0.0/16"
caddy_runtime_id = "10001"
seed_caddy_snippets = ["tls.caddy", "netbird.caddy"]
seed_fabric_dirs = [
    ("", "755"),
    ("stacks", "755"),
    ("secrets", "755"),
    ("secrets/caddy", "750"),
    ("data", "755"),
    ("logs", "755"),
    ("backups", "750"),
]

if not (registry_user and registry_token and cf_acme_token):
    raise ValueError(
        "Set PLATFORM_DEPLOY_USERNAME, PLATFORM_DEPLOY_TOKEN, CF_ACME_TOKEN "
        "in secrets.sops.env"
    )

if not acme_email:
    raise ValueError("Set ACME_EMAIL in secrets.sops.env")

stacks_src = platform_git_root / "stacks" / "caddy"
stack_dest = f"{platform_root}/stacks/caddy"
caddy_config_dir = f"{stack_dest}/config"
caddy_snippets_dir = f"{caddy_config_dir}/snippets"
caddy_sites_dir = f"{caddy_config_dir}/sites"

for path, mode in seed_fabric_dirs:
    files.directory(
        name=f"Create {path or platform_root}",
        path=f"{platform_root}/{path}".rstrip("/"),
        user=admin_user,
        group=admin_user,
        mode=mode,
    )

for path in ("data/caddy", "logs/caddy"):
    files.directory(
        name=f"Create Caddy runtime directory {path}",
        path=f"{platform_root}/{path}",
        user=caddy_runtime_id,
        group=caddy_runtime_id,
        mode="750",
    )

server.shell(
    name="Own existing Caddy runtime data",
    commands=[
        f"chown -R {caddy_runtime_id}:{caddy_runtime_id} "
        f"{platform_root}/data/caddy {platform_root}/logs/caddy"
    ],
)

files.directory(
    name="Create Caddy config/snippets",
    path=caddy_snippets_dir,
    user=admin_user,
    group=admin_user,
    mode="755",
)

caddyfile = files.template(
    name="Write Caddyfile",
    src=str(stacks_src / "config" / "Caddyfile.j2"),
    dest=f"{caddy_config_dir}/Caddyfile",
    user=admin_user,
    group=admin_user,
    mode="644",
    platform_seed_caddy=[
        snippet
        for snippet in seed_caddy_snippets
        if snippet != "tls.caddy"
    ],
)

for snippet in seed_caddy_snippets:
    files.template(
        name=f"Write Caddy snippet {snippet}",
        src=str(stacks_src / "config" / "snippets" / f"{snippet}.j2"),
        dest=f"{caddy_snippets_dir}/{snippet}",
        user=admin_user,
        group=admin_user,
        mode="644",
        acme_email=acme_email,
        proxy_subnet=proxy_subnet,
        acme_zone=acme_zone,
    )

files.directory(
    name="Create Caddy sites dir",
    path=caddy_sites_dir,
    user=admin_user,
    group=admin_user,
    mode="755",
)

files.put(
    name="Write empty Caddy site",
    src=io.StringIO("# sites are installed by platform-deploy\n"),
    dest=f"{caddy_sites_dir}/_.caddy",
    user=admin_user,
    group=admin_user,
    mode="644",
)

files.put(
    name="Sync Caddy compose.yaml",
    src=str(stacks_src / "compose.yaml"),
    dest=f"{stack_dest}/compose.yaml",
    user=admin_user,
    group=admin_user,
    mode="644",
)

caddy_env = files.template(
    name="Write Caddy environment",
    src=str(stacks_src / "config" / "caddy.env.j2"),
    dest=f"{platform_root}/secrets/caddy/caddy.env",
    user=admin_user,
    group=admin_user,
    mode="600",
    cf_acme_token=cf_acme_token,
)

caddy_dotenv = files.put(
    name="Write Caddy .env",
    src=io.StringIO(
        f"CADDY_IMAGE={host.data.gitlab_caddy_image}\n"
    ),
    dest=f"{stack_dest}/.env",
    user=admin_user,
    group=admin_user,
    mode="600",
)

docker.login(
    name="Login GitLab registry",
    server=registry_server,
    username=registry_user,
    password=registry_token,
    force=True,
)

docker.network(
    name="Create proxy network",
    network="proxy",
    subnet=proxy_subnet,
    present=True,
)

proxy_network = host.get_fact(DockerNetwork, object_id="proxy") or []
proxy_subnets = {
    ipam["Subnet"]
    for network in proxy_network
    for ipam in (network.get("IPAM", {}).get("Config") or [])
    if ipam.get("Subnet")
}

if proxy_network and proxy_subnets != {proxy_subnet}:
    raise ValueError(
        f"proxy network subnet {sorted(proxy_subnets)} != {proxy_subnet}: "
        "recreate it manually, see README.md 'Сеть proxy'"
    )

docker.compose(
    name="Start Caddy",
    project_directory=stack_dest,
    present=True,
    pull=pull_policy,
    remove_orphans=True,
)

caddy_recreate_operations = [caddy_env, caddy_dotenv, caddyfile]

docker.compose(
    name="Recreate Caddy on env change",
    project_directory=stack_dest,
    present=True,
    force_recreate=True,
    remove_orphans=True,
    _if=any_changed(*caddy_recreate_operations),
)

server.shell(
    name="Reload Caddy",
    commands=[
        "docker exec caddy caddy reload "
        "--config /etc/caddy/Caddyfile --adapter caddyfile"
    ],
)

records = customer_dns_records()
tunnel_enabled = bool(tunnel.hostnames(records, host_name))

if tunnel_enabled:
    tunnel_zone = tunnel.zone_of(records, host_name)

    cloudflared_dest = f"{platform_root}/stacks/cloudflared"
    cloudflared_secrets_dir = f"{platform_root}/secrets/cloudflared"

    for path in (cloudflared_dest, cloudflared_secrets_dir):
        files.directory(
            name=f"Create {path}",
            path=path,
            user=admin_user,
            group=admin_user,
            mode="750",
        )

    files.put(
        name="Sync cloudflared compose.yaml",
        src=str(
            platform_git_root
            / "stacks"
            / "cloudflared"
            / "compose.yaml"
        ),
        dest=f"{cloudflared_dest}/compose.yaml",
        user=admin_user,
        group=admin_user,
        mode="644",
    )

    cloudflared_env = files.put(
        name="Write cloudflared tunnel token",
        src=io.StringIO(
            f"TUNNEL_TOKEN={tunnel.token(tunnel_zone, host_name)}\n"
        ),
        dest=f"{cloudflared_secrets_dir}/cloudflared.env",
        user=admin_user,
        group=admin_user,
        mode="600",
    )

    docker.compose(
        name="Start cloudflared",
        project_directory=cloudflared_dest,
        present=True,
        pull=pull_policy,
        remove_orphans=True,
    )

    docker.compose(
        name="Recreate cloudflared on token change",
        project_directory=cloudflared_dest,
        present=True,
        force_recreate=True,
        _if=cloudflared_env.did_change,
    )
else:
    cloudflared_dest = f"{platform_root}/stacks/cloudflared"
    cloudflared_compose = f"{cloudflared_dest}/compose.yaml"
    server.shell(
        name="Stop obsolete cloudflared",
        commands=[
            f"if [ -f {shlex.quote(f'{cloudflared_dest}/compose.yaml')} ]; then "
            f"docker compose --project-directory {shlex.quote(cloudflared_dest)} "
            "down --remove-orphans; fi"
        ],
        _if=lambda: bool(host.get_fact(File, path=cloudflared_compose)),
    )
    files.file(
        name="Remove obsolete cloudflared compose",
        path=cloudflared_compose,
        present=False,
    )
