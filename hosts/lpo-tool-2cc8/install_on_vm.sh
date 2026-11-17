#!/usr/bin/env bash
# Install or refresh lpo-tool-2cc8 on its VM, the way the agent-fleet boot program installs a
# host (boot/fleet_boot.py): secrets as owner-only files for the Node's user, .env, the
# metadata-server firewall, the Docker drop-in and the compose unit, then start and wait.
#
# Run as root from a directory holding this bundle:  sudo bash install_on_vm.sh
# Secrets come from Secret Manager with the VM's own account; nothing is printed.
set -euo pipefail
project=naap-orchestrator
prefix=lpo-tool-2cc8
host=/opt/lpo/host
node_uid=65532  # agent-fleet boot_metadata.py NODE_UID
src=$(cd "$(dirname "$0")" && pwd)

install -d -m 0755 "$host"
install -d -m 0700 "$host/secrets"
for f in compose.yaml config.toml Caddyfile host.json; do install -m 0644 "$src/$f" "$host/$f"; done
install -m 0755 "$src/metadata-firewall.sh" "$host/metadata-firewall.sh"

secret() {  # secret id, target file: owner-only for the Node's user
  local tmp; tmp=$(mktemp "$host/secrets/.tmp.XXXXXX")
  gcloud secrets versions access latest --secret "$1" --project "$project" > "$tmp"
  chown "$node_uid:$node_uid" "$tmp"; chmod 0400 "$tmp"; mv -f "$tmp" "$2"
}
secret "$prefix-wallet-keystore" "$host/secrets/wallet-keystore"
secret "$prefix-wallet-password" "$host/secrets/wallet-password"
secret "$prefix-arbitrum-rpc-url" "$host/secrets/arbitrum-rpc-url"

# Node's bootstrap secret = the runners' ORCH_SECRET. Made once on this VM, kept across refreshes.
if [[ ! -s $host/secrets/runner-bootstrap ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(36), end="")' > "$host/secrets/runner-bootstrap"
fi
chown "$node_uid:$node_uid" "$host/secrets/runner-bootstrap"; chmod 0400 "$host/secrets/runner-bootstrap"
umask 077
printf "COMPOSE_PROFILES='media,hyperframes'\nGCS_BUCKET='%s-outputs'\nORCH_SECRET='%s'\n" \
  "$prefix" "$(cat "$host/secrets/runner-bootstrap")" > "$host/.env"
umask 022

install -d -m 0755 /etc/systemd/system/docker.service.d
install -m 0644 "$src/docker-dropin.conf" /etc/systemd/system/docker.service.d/lpo-metadata-firewall.conf
install -m 0644 "$src/lpo-tool.service" /etc/systemd/system/lpo-tool.service
"$host/metadata-firewall.sh"

# The unit never pulls (--pull never), as on the fleet: pull the pinned images here.
(cd "$host" && docker compose pull -q)
systemctl daemon-reload
systemctl enable -q lpo-tool.service
systemctl restart lpo-tool.service
(cd "$host" && docker compose ps --format '{{.Service}} {{.State}} {{.Health}}')
