from pyinfra import host
from pyinfra.operations import server


if host.data.is_monitor_host:
    server.shell(
        name="Silence monitoring during bootstrap",
        commands=[
            r"""
if ! command -v docker >/dev/null 2>&1 \
  || [ "$(docker inspect -f '{{.State.Running}}' lab-alertmanager 2>/dev/null || true)" != true ]; then
  echo "skip monitoring silence: Alertmanager is not running"
  exit 0
fi

run_amtool() {
  docker exec lab-alertmanager amtool \
    --alertmanager.url=http://127.0.0.1:9093 "$@"
}

if ! run_amtool silence query -q >/dev/null 2>&1; then
  echo "skip monitoring silence: Alertmanager API is unavailable"
  exit 0
fi

matcher='alertname=~"EndpointDown|BlackboxExporterDown"'
if [ -n "$(run_amtool silence query -q "$matcher")" ]; then
  echo "skip monitoring silence: already active"
  exit 0
fi

run_amtool silence add \
  --duration=30m \
  --author=platform-bootstrap \
  --comment="planned platform bootstrap" \
  "$matcher"
"""
        ],
    )
