#!/bin/sh
# /opt/custos/bootstrap.sh — idempotent provisioning of the custos LXC.
# Source files live in the repo's turn/ directory; this installs them to
# their active locations and bootstraps the stack. Run from blink1:
#   sudo -n pct exec 122 -- sh /tmp/bootstrap.sh
set -eu
SRC="${1:-/opt/custos/repo/turn}"
export DEBIAN_FRONTEND=noninteractive

# 1. Timezone: the watch runs 00:00-05:00 America/Denver
ln -sf /usr/share/zoneinfo/America/Denver /etc/localtime
echo "America/Denver" > /etc/timezone

# 2. Base packages (cron ships in the template; the rest are cheap)
apt-get update -qq
apt-get install -y -qq git cron ca-certificates curl openssh-client >/dev/null

# 3. Node 22 — house layout /opt/node (same as wsim 119)
if [ ! -x /opt/node/bin/node ]; then
  VER="${NODE_VERSION:-node-v22.23.2-linux-x64}"
  curl -fsSL "https://nodejs.org/dist/${VER}/${VER}.tar.xz" -o /tmp/node.tar.xz
  mkdir -p /opt/node
  tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1
  rm -f /tmp/node.tar.xz
fi
printf 'export PATH=/opt/node/bin:$PATH\n' > /etc/profile.d/node.sh

# 4. pi harness, pinned (upgrade: bump PI_VERSION, re-run)
PI_VERSION="${PI_VERSION:-0.73.1}"
if [ "$(/opt/node/bin/pi --version 2>/dev/null || echo none)" != "$PI_VERSION" ]; then
  /opt/node/bin/npm install -g "@mariozechner/pi-coding-agent@${PI_VERSION}" >/dev/null
fi

# 5. pi configuration: the ninfer provider (johan's 5090)
mkdir -p /root/.pi/agent
install -m 644 "$SRC/models.json"   /root/.pi/agent/models.json
install -m 644 "$SRC/settings.json" /root/.pi/agent/settings.json

# 6. Cron + turn entrypoint
install -m 755 "$SRC/turn.sh"     /opt/custos/turn.sh
install -m 644 "$SRC/cron.custos" /etc/cron.d/custos
systemctl enable cron >/dev/null
systemctl is-active cron >/dev/null 2>&1 || systemctl start cron

# 7. Repo clone (deploy key at /opt/custos/git-deploy.key, pushed out-of-band)
if [ ! -d /opt/custos/repo/.git ]; then
  rm -rf /opt/custos/repo
  mkdir -p /opt/custos/repo
  GIT_SSH_COMMAND="ssh -i /opt/custos/git-deploy.key -o StrictHostKeyChecking=no" \
    git clone git@github.com:collettiquette/custos.git /opt/custos/repo
fi

# 8. Runtime state + logs
mkdir -p /opt/custos/repo/.state /var/log/custos
chmod 600 /etc/custos.env 2>/dev/null || true
echo "bootstrap done: $(/opt/node/bin/node --version) / pi $(/opt/node/bin/pi --version 2>/dev/null)"
