from pyinfra import host
from pyinfra.operations import files, server, systemd

admin_user = host.data.admin_user
ssh_port = host.data.ssh_port

server.user(
    name="Lock root password",
    user="root",
    password="!",
)

files.directory(
    name="Create SSH directory",
    path="/run/sshd",
    user="root",
    group="root",
    mode="755",
)

ssh_config = files.template(
    name="Install SSH hardening config",
    src="templates/99-hardening.conf.j2",
    dest="/etc/ssh/sshd_config.d/99-hardening.conf",
    user="root",
    group="root",
    mode="644",
    ssh_port=ssh_port,
    admin_user=admin_user,
    deploy_user=host.data.deploy_user,
)

server.shell(
    name="Validate SSH config",
    commands=["sshd -t"],
    _if=ssh_config.did_change,
)

systemd.service(
    name="Reload SSH",
    service="ssh",
    reloaded=True,
    _if=ssh_config.did_change,
)
