import base64
import io
import re
import secrets

from pyinfra import host
from pyinfra.facts.server import Command
from pyinfra.operations import apt, files, server, systemd
from pyinfra.operations.util import any_changed

from edge_infra.host import (
    configure_admin_user,
    configure_dpkg_lock,
    configure_fail2ban,
    configure_ufw,
)

import firewall

admin_user = host.data.admin_user
breakglass_user = host.data.breakglass_user
platform_root = host.data.platform_root
ssh_public_key = host.data.ssh_public_key
fail2ban_ignoreip = ("127.0.0.1/8", "::1")
target_hostname = host.data.hostname

resolved_conf = files.put(
    name="Configure systemd-resolved upstream DNS",
    src=io.StringIO("[Resolve]\nDNS=1.1.1.1 8.8.8.8\nFallbackDNS=1.0.0.1 8.8.4.4\n"),
    dest="/etc/systemd/resolved.conf.d/platform-dns.conf",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable systemd-resolved",
    service="systemd-resolved",
    running=True,
    enabled=True,
)

systemd.service(
    name="Restart systemd-resolved",
    service="systemd-resolved",
    restarted=True,
    _if=resolved_conf.did_change,
)

configure_dpkg_lock(path="/etc/apt/apt.conf.d/50platform-lock-timeout")

apt.update(
    name="Update apt cache",
    cache_time=3600,
)

apt.packages(
    name="Install packages",
    packages=[
        "curl",
        "wget",
        "git",
        "unzip",
        "htop",
        "nano",
        "jq",
        "ufw",
        "fail2ban",
        "unattended-upgrades",
        "libpam-google-authenticator",
    ],
    update=False,
)

files.put(
    name="Enable automatic Ubuntu security updates",
    src=io.StringIO(
        'APT::Periodic::Update-Package-Lists "1";\n'
        'APT::Periodic::Unattended-Upgrade "1";\n'
    ),
    dest="/etc/apt/apt.conf.d/20auto-upgrades",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Configure safe unattended upgrades",
    src=io.StringIO(
        'Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";\n'
        'Unattended-Upgrade::Remove-New-Unused-Dependencies "true";\n'
        'Unattended-Upgrade::Automatic-Reboot "false";\n'
    ),
    dest="/etc/apt/apt.conf.d/52platform-unattended-upgrades",
    user="root",
    group="root",
    mode="644",
)

server.hostname(
    name="Set hostname",
    hostname=target_hostname,
)

files.line(
    name="Map hostname in /etc/hosts",
    path="/etc/hosts",
    line=r"^127\.0\.1\.1[[:space:]].*$",
    replace=f"127.0.1.1 {target_hostname}",
)

files.put(
    name="Install public IP",
    src=io.StringIO(f"{host.data.public_ip}\n"),
    dest=f"{platform_root}/public_ip",
    user="root",
    group="root",
    mode="644",
)

breakglass_dir = f"{platform_root}/secrets/breakglass"
breakglass_password_path = f"{breakglass_dir}/password"
breakglass_otp_secret_path = f"{breakglass_dir}/otp_secret"

files.directory(
    name="Create breakglass directory",
    path=breakglass_dir,
    user="root",
    group="root",
    mode="700",
)

existing_breakglass_password = (
    host.get_fact(
        Command, command=f"cat {breakglass_password_path} 2>/dev/null || true"
    )
    or ""
).strip()
existing_breakglass_otp_secret = (
    host.get_fact(
        Command, command=f"cat {breakglass_otp_secret_path} 2>/dev/null || true"
    )
    or ""
).strip()

breakglass_password = existing_breakglass_password or base64.b64encode(
    secrets.token_bytes(32)
).decode()
breakglass_otp_secret = (
    existing_breakglass_otp_secret or base64.b32encode(secrets.token_bytes(20)).decode()
).upper()

if len(breakglass_password) < 16 or not re.fullmatch(r"[A-Z2-7]+=*", breakglass_otp_secret):
    raise ValueError("Breakglass secrets invalid.")

breakglass_password_file = files.put(
    name="Install breakglass password",
    src=io.StringIO(breakglass_password),
    dest=breakglass_password_path,
    user="root",
    group="root",
    mode="600",
)

files.put(
    name="Install breakglass OTP secret",
    src=io.StringIO(breakglass_otp_secret),
    dest=breakglass_otp_secret_path,
    user="root",
    group="root",
    mode="600",
)

configure_admin_user(admin_user=admin_user, ssh_public_key=ssh_public_key)

breakglass_user_op = server.user(
    name=f"Create {breakglass_user} user",
    user=breakglass_user,
    groups=["sudo"],
    shell="/bin/bash",
    create_home=True,
    append=True,
)

server.shell(
    name=f"Set {breakglass_user} password",
    commands=[f"{{ printf '%s:' {breakglass_user}; cat {breakglass_password_path}; echo; }} | chpasswd"],
    _if=any_changed(breakglass_password_file, breakglass_user_op),
)

files.template(
    name="Install breakglass TOTP",
    src="templates/google_authenticator.j2",
    dest=f"/home/{breakglass_user}/.google_authenticator",
    user=breakglass_user,
    group=breakglass_user,
    mode="400",
    breakglass_otp_secret=breakglass_otp_secret,
)

breakglass_sudo = files.put(
    name=f"Install {breakglass_user} sudo",
    src=io.StringIO(f"{breakglass_user} ALL=(ALL:ALL) ALL\n"),
    dest=f"/etc/sudoers.d/{breakglass_user}",
    user="root",
    group="root",
    mode="440",
)

server.shell(
    name=f"Validate {breakglass_user} sudo",
    commands=[f"visudo -cf /etc/sudoers.d/{breakglass_user}"],
    _if=breakglass_sudo.did_change,
)

files.block(
    name="Enable TOTP for login",
    path="/etc/pam.d/login",
    content="auth required pam_google_authenticator.so nullok echo_verification_code",
    line="@include common-auth",
    after=True,
    marker="# {mark} platform google-authenticator",
)

ufw_allow = firewall.ufw_rules(host)
configure_ufw(rules=ufw_allow)
configure_fail2ban(ignore_ips=fail2ban_ignoreip)
