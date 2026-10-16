from pyinfra import host, local
from pyinfra.operations import server

local.include("tasks/monitor_silence.py")
local.include("tasks/common.py")
local.include("tasks/netbird.py")
local.include("tasks/docker.py")
local.include("tasks/lazydocker.py")
local.include("tasks/lockdown.py")
local.include("tasks/seed.py")
local.include("tasks/sops.py")
local.include("tasks/restic.py")
local.include("tasks/deploy.py")
local.include("tasks/monitor_labs.py")
local.include("tasks/docker_fw.py")

server.shell(
    name="Reboot if required",
    commands=["( sleep 1 && reboot ) </dev/null >/dev/null 2>&1 &"],
    _if=lambda: host.run_shell_command("test -f /var/run/reboot-required")[0],
)
