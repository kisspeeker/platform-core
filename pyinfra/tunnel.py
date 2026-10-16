# Docs: https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request



def _fqdns(block: dict, name: str):
    for sub, rec in (block.get("records") or {}).items():
        if rec.get("host") == name and rec.get("proxied", True):
            yield block["zone"] if sub == "apex" else f"{sub}.{block['zone']}"


def _proxied(records: dict, name: str):
    """(zone, fqdn) of proxied Cloudflare records of host `name` (mirrors tofu/records.tf)."""
    for block in records["zones"].values():
        if block["backend"] != "cloudflare":
            continue
        for fqdn in _fqdns(block, name):
            yield block["zone"], fqdn


def hostnames(records: dict, name: str) -> list[str]:
    """Public domains served by the tunnel of host `name`."""
    return [fqdn for _, fqdn in _proxied(records, name)]


def _get(path: str):
    from group_data.all import cf_admin_token, cloudflare_api

    if not cf_admin_token:
        raise RuntimeError(
            "Set CF_ADMIN_TOKEN in secrets.sops.env "
            "(Zone Read + Cloudflare One Connector: cloudflared Edit)"
        )
    req = urllib.request.Request(
        f"{cloudflare_api}{path}",
        headers={"Authorization": f"Bearer {cf_admin_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cloudflare {path}: HTTP {e.code} {e.read().decode(errors='replace')}") from None
    if not body.get("success"):
        raise RuntimeError(f"Cloudflare {path}: {body.get('errors')}")
    return body["result"]


def zone_of(records: dict, name: str) -> str:
    return next(z for z, _ in _proxied(records, name))


def token(zone: str, name: str) -> str:
    """Account comes from the zone (Zone Read), so the token needs no account-level listing."""
    zones = _get("/zones?" + urllib.parse.urlencode({"name": zone}))
    if not zones:
        raise RuntimeError(f"zone {zone} not visible to CF_ADMIN_TOKEN")
    account = zones[0]["account"]["id"]
    query = urllib.parse.urlencode({"name": name, "is_deleted": "false"})
    if not (tunnels := _get(f"/accounts/{account}/cfd_tunnel?{query}")):
        raise RuntimeError(
            f"tunnel {name} not found: run make tf-apply (customers/<name>/dns/tofu)"
        )
    return _get(f"/accounts/{account}/cfd_tunnel/{tunnels[0]['id']}/token")
