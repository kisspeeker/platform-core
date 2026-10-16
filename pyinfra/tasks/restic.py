import base64
import io
import json
import secrets
import shlex
from pathlib import Path

from pyinfra import host
from pyinfra.facts.server import Arch, Command
from pyinfra.operations import apt, files, python, server

import mesh
import sops_env

if not host.data.restic_enabled:
    pass
else:
    platform_root = host.data.platform_root
    platform_git_root = Path(__file__).resolve().parents[1]
    target_hostname = host.data.hostname
    is_core = mesh.CORE in host.groups
    owns_backup = is_core

    restic_repository = host.data.restic_primary_repository
    restic_aws_access_key_id = host.data.restic_primary_access_key_id
    restic_aws_secret_access_key = host.data.restic_primary_secret_access_key
    restic_aws_default_region = host.data.restic_primary_region

    offsite_enabled = host.data.restic_offsite_enabled
    offsite_repository = host.data.restic_offsite_repository
    offsite_access_key_id = host.data.restic_offsite_access_key_id
    offsite_secret_access_key = host.data.restic_offsite_secret_access_key
    offsite_region = host.data.restic_offsite_region

    restic_version = "0.18.1"
    restic_checksums = {
        "amd64": "680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d",
        "arm64": "87f53fddde38764095e9c058a3b31834052c37e5826d2acf34e18923c006bd45",
    }
    resticprofile_version = "0.33.1"
    resticprofile_checksums = {
        "amd64": "1d7027d15e3e2456e585a210f811d0f72ec40f6b3388f00425642ed579165d70",
        "arm64": "58e4fd6fbe2bd460ee6823c3693f39a10f0f24c90635f0215c19b0ab20bbf5df",
    }
    restic_backup_schedule = "*-*-* 03:00:00"
    restic_offsite_schedule = "*-*-* 04:30:00"
    restic_forget_schedule = "Sun *-*-* 04:00:00"
    restic_check_schedule = "Sun *-*-* 06:00:00"
    restic_lock_wait = "30m"
    restic_check_read_data_subset = "5%"
    restic_backup_timeout = "12h"
    restic_keep_within = "30d"
    restic_keep_daily = 7
    restic_keep_weekly = 4
    restic_keep_monthly = 6

    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(host.get_fact(Arch), "")
    if arch not in restic_checksums or arch not in resticprofile_checksums:
        raise ValueError(f"Unsupported Restic architecture: {host.get_fact(Arch)}")

    password_path = "/etc/platform-restic.password"
    offsite_password_path = "/etc/platform-restic-offsite.password"
    env_path = "/etc/platform-restic.env"
    offsite_env_path = "/etc/platform-restic-offsite.env"
    hooks_dir = "/usr/local/lib/platform-restic"
    hooks_src = platform_git_root / "files" / "restic"
    cache_dir = f"/var/cache/platform-resticprofile/{resticprofile_version}-{arch}"
    tarball = (
        f"/var/cache/platform-resticprofile/"
        f"resticprofile-{resticprofile_version}-{arch}.tar.gz"
    )
    tarball_url = (
        "https://github.com/creativeprojects/resticprofile/releases/download/"
        f"v{resticprofile_version}/"
        f"resticprofile_no_self_update_{resticprofile_version}_linux_{arch}.tar.gz"
    )
    restic_cache_dir = f"/var/cache/platform-restic/{restic_version}-{arch}"
    restic_archive = (
        f"/var/cache/platform-restic/restic_{restic_version}_linux_{arch}.bz2"
    )
    restic_url = (
        "https://github.com/restic/restic/releases/download/"
        f"v{restic_version}/restic_{restic_version}_linux_{arch}.bz2"
    )
    restic_bin = "/usr/local/bin/restic"

    files.directory(
        name="Create Restic download cache",
        path="/var/cache/platform-restic",
        user="root",
        group="root",
        mode="755",
    )

    files.directory(
        name="Create Restic extract cache",
        path=restic_cache_dir,
        user="root",
        group="root",
        mode="755",
    )

    files.download(
        name="Download Restic",
        src=restic_url,
        dest=restic_archive,
        mode="644",
        sha256sum=restic_checksums[arch],
    )

    restic_version_out = (
        host.get_fact(
            Command,
            command=f"{restic_bin} version 2>/dev/null || true",
        )
        or ""
    )

    if restic_version not in restic_version_out:
        apt.packages(
            name="Install bzip2",
            packages=["bzip2"],
        )
        server.shell(
            name="Install Restic binary",
            commands=[
                f"bunzip2 -c {shlex.quote(restic_archive)} > "
                f"{shlex.quote(restic_cache_dir + '/restic')} && "
                f"install -m 0755 {shlex.quote(restic_cache_dir + '/restic')} "
                f"{shlex.quote(restic_bin)}"
            ],
        )

    files.directory(
        name="Create Resticprofile cache",
        path=cache_dir,
        user="root",
        group="root",
        mode="755",
    )

    files.download(
        name="Download Resticprofile",
        src=tarball_url,
        dest=tarball,
        mode="644",
        sha256sum=resticprofile_checksums[arch],
    )

    resticprofile_version_out = (
        host.get_fact(
            Command,
            command="/usr/local/bin/resticprofile version 2>/dev/null || true",
        )
        or ""
    )

    if resticprofile_version not in resticprofile_version_out:
        server.shell(
            name="Install Resticprofile binary",
            commands=[
                f"tar -xzf {shlex.quote(tarball)} -C {shlex.quote(cache_dir)} && "
                f"install -m 0755 {shlex.quote(cache_dir + '/resticprofile')} "
                "/usr/local/bin/resticprofile"
            ],
        )

    files.directory(
        name="Create Restic hooks directory",
        path=hooks_dir,
        user="root",
        group="root",
        mode="755",
    )

    for script in ("containers", "restore"):
        files.put(
            name=f"Install Restic {script} script",
            src=str(hooks_src / script),
            dest=f"{hooks_dir}/{script}",
            user="root",
            group="root",
            mode="755",
        )

    backup_sources = "\n".join(
        f"      - {json.dumps(f'{platform_root}/{d}')}"
        for d in ("data", "backups", "secrets")
    )
    backup_hooks = f"""    run-before:
      - {json.dumps(f"{hooks_dir}/containers stop label=platform.backup.stop=true")}
    run-finally:
      - {json.dumps(f"{hooks_dir}/containers start")}
"""

    profile_path = "/etc/platform-resticprofile.yaml"
    dropin_name = "platform-restic-systemd.conf"
    healthchecks_ping_key = host.data.healthchecks_ping_key
    if not healthchecks_ping_key:
        raise ValueError(
            "HEALTHCHECKS_PING_KEY is required when Restic backups are enabled"
        )

    def healthchecks_url(job: str) -> str:
        slug = f"{target_hostname}-{job}"
        return f"https://hc-ping.com/{healthchecks_ping_key}/{slug}"

    def notify(job: str):
        url = healthchecks_url(job)
        failure_body = (
            f"Restic job {job} failed on {target_hostname}. "
            "Check the local systemd journal for details."
        )
        return f"""    send-before:
      - url: {json.dumps(f'{url}/start?create=1')}
        method: HEAD
    send-after:
      - url: {json.dumps(f'{url}?create=1')}
        method: HEAD
    send-after-fail:
      - url: {json.dumps(f'{url}/fail?create=1')}
        method: POST
        headers:
          - name: Content-Type
            value: text/plain; charset=UTF-8
        body: {json.dumps(failure_body)}
"""

    for template_path in (
        "/etc/platform-restic-success.tmpl",
        "/etc/platform-restic-fail.tmpl",
    ):
        files.file(
            name=f"Remove legacy {Path(template_path).name}",
            path=template_path,
            present=False,
        )

    def schedule(calendar, job):
        return f"""    schedule: {json.dumps(calendar)}
    schedule-permission: system
    schedule-lock-wait: {restic_lock_wait}
{notify(job)}"""

    profile_yaml = f"""version: "1"
global:
  restic-binary: /usr/local/bin/restic
  nice: 10
  ionice: true
  ionice-class: 2
  ionice-level: 7
  restic-lock-retry-after: "1m"
platform:
  env-file: {env_path}
  force-inactive-lock: true
  systemd-drop-in-files:
    - {dropin_name}
  backup:
    source:
{backup_sources}
    host: {json.dumps(target_hostname)}
{backup_hooks}{schedule(restic_backup_schedule, "backup-primary")}  snapshots:
    host: {json.dumps(target_hostname)}
"""

    if owns_backup:
        profile_yaml += f"""  forget:
    group-by: host,paths
    keep-within: {json.dumps(restic_keep_within)}
    keep-daily: {restic_keep_daily}
    keep-weekly: {restic_keep_weekly}
    keep-monthly: {restic_keep_monthly}
    prune: true
{schedule(restic_forget_schedule, "forget-primary")}  check:
    read-data-subset: {json.dumps(restic_check_read_data_subset)}
{schedule(restic_check_schedule, "check-primary")}"""

    if offsite_enabled:
        profile_yaml += f"""offsite:
  env-file: {offsite_env_path}
  force-inactive-lock: true
  systemd-drop-in-files:
    - {dropin_name}
  backup:
    source:
{backup_sources}
    host: {json.dumps(target_hostname)}
{backup_hooks}{schedule(restic_offsite_schedule, "backup-offsite")}  snapshots:
    host: {json.dumps(target_hostname)}
"""

    restic_profile = files.put(
        name="Write Resticprofile config",
        src=io.StringIO(profile_yaml),
        dest=profile_path,
        user="root",
        group="root",
        mode="600",
    )

    restic_dropin = files.put(
        name="Write Resticprofile systemd drop-in",
        src=io.StringIO(
            "[Unit]\n"
            "After=docker.service\n"
            "Requires=docker.service\n"
            "\n"
            "[Service]\n"
            f"ExecStopPost={hooks_dir}/containers start\n"
            f"TimeoutStartSec={restic_backup_timeout}\n"
            "UMask=0077\n"
        ),
        dest=f"/etc/{dropin_name}",
        user="root",
        group="root",
        mode="644",
    )

    def install_password(
        env_name: str,
        given: str,
        stored_path: str,
        *,
        required: bool,
        generate: bool = False,
    ) -> str:
        if not is_core:
            if required and len(given) < 16:
                raise ValueError(f"{env_name} missing or too short")
            return given

        stored = (
            host.get_fact(Command, command=f"cat {stored_path} 2>/dev/null || true")
            or ""
        ).strip()
        password_mismatch = stored and given and stored != given
        if password_mismatch:
            if not host.data.restic_rotate_password:
                raise ValueError(
                    f"{env_name} does not match password stored on core; "
                    "set RESTIC_ROTATE_PASSWORD=true to rotate"
                )
            password = given
        else:
            password = stored or given
        if not password:
            if not (required and generate):
                if required:
                    raise ValueError(f"{env_name} missing or too short")
                return ""
            password = base64.b64encode(secrets.token_bytes(32)).decode()
        files.put(
            name=f"Install {env_name}",
            src=io.StringIO(password),
            dest=stored_path,
            user="root",
            group="root",
            mode="600",
        )
        if password != given:
            def save(key=env_name, value=password):
                sops_env.set_secrets(**{key: value})

            python.call(name=f"Save {env_name} to sops", function=save)
        return password

    primary_password = install_password(
        host.data.restic_password_primary_key,
        host.data.restic_password_primary,
        password_path,
        required=True,
        generate=True,
    )
    offsite_password = ""
    if offsite_enabled:
        offsite_password = install_password(
            host.data.restic_password_offsite_key,
            host.data.restic_password_offsite,
            offsite_password_path,
            required=True,
        )

    repositories = {
        env_path: (
            restic_repository,
            restic_aws_access_key_id,
            restic_aws_secret_access_key,
            restic_aws_default_region,
            primary_password,
        ),
    }
    if offsite_enabled:
        repositories[offsite_env_path] = (
            offsite_repository,
            offsite_access_key_id,
            offsite_secret_access_key,
            offsite_region,
            offsite_password,
        )

    for path, (repository, key_id, secret, region, password) in repositories.items():
        files.put(
            name=f"Write {path}",
            src=io.StringIO(
                f"RESTIC_REPOSITORY={shlex.quote(repository)}\n"
                f"RESTIC_PASSWORD={shlex.quote(password)}\n"
                f"PLATFORM_RESTIC_HOST={shlex.quote(target_hostname)}\n"
                f"PLATFORM_ROOT={shlex.quote(platform_root)}\n"
                f"AWS_ACCESS_KEY_ID={shlex.quote(key_id)}\n"
                f"AWS_SECRET_ACCESS_KEY={shlex.quote(secret)}\n"
                f"AWS_DEFAULT_REGION={shlex.quote(region)}\n"
            ),
            dest=path,
            user="root",
            group="root",
            mode="600",
        )

    def check_restic_runtime():
        sudo = {"_sudo": True}

        def ensure_repository(path):
            ok, output = host.run_shell_command(
                f"set -a; . {path}; set +a; "
                "/usr/local/bin/restic cat config 2>&1 >/dev/null; echo __RC:$?",
                **sudo,
            )
            lines = output.stdout_lines or output.stdout.splitlines()
            rc_line = [line for line in lines if line.startswith("__RC:")]
            rc = int(rc_line[-1].split(":", 1)[1]) if rc_line else 1
            stderr = "\n".join(
                line for line in lines if not line.startswith("__RC:")
            )
            if rc == 0:
                return
            if rc == 10 or "repository does not exist" in stderr:
                ok_init, out_init = host.run_shell_command(
                    f"set -a; . {path}; set +a; /usr/local/bin/restic init",
                    **sudo,
                )
                if not ok_init:
                    raise RuntimeError(
                        f"restic init failed ({path}): {out_init.stderr}"
                    )
                return
            raise RuntimeError(
                f"restic cat config failed ({path}, rc={rc}): {stderr}"
            )

        ok, output = host.run_shell_command(
            f"/usr/local/bin/resticprofile --config {profile_path} "
            "--name platform show",
            **sudo,
        )
        if not ok:
            raise RuntimeError(f"Invalid resticprofile config: {output.stderr}")

        if owns_backup:
            for path in repositories:
                ensure_repository(path)

    python.call(
        name="Check Restic runtime",
        function=check_restic_runtime,
    )

    timers = host.get_fact(
        Command,
        command=(
            "systemctl list-timers --all --no-legend "
            "'resticprofile-*' 2>/dev/null || true"
        ),
    ) or ""
    server.shell(
        name="Schedule Resticprofile",
        commands=[
            f"/usr/local/bin/resticprofile --config {profile_path} schedule --all",
        ],
        _if=lambda: (
            restic_profile.did_change()
            or restic_dropin.did_change()
            or "profile-platform" not in timers
        ),
    )
