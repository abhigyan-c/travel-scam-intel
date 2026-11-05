#!/usr/bin/env bash
# Initial Ubuntu 24.04 host setup; run as root over the already verified tailnet.
# Supply the dedicated deployment PUBLIC key as the only argument.
set -euo pipefail
test "$(id -u)" = 0
source /etc/os-release
test "$ID" = ubuntu
test "$VERSION_ID" = 24.04
key="${1:?Pass the deployment public-key file}"
ssh-keygen -lf "$key" >/dev/null
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg python3 python3-venv unzip
python3 -m venv /opt/travel-scam-awscli
/opt/travel-scam-awscli/bin/pip install 'awscli==1.42.0'
ln -sfn /opt/travel-scam-awscli/bin/aws /usr/local/bin/aws
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n' > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
curl -fsSL https://github.com/bitwarden/sdk-sm/releases/download/bws-v2.1.0/bws-x86_64-unknown-linux-gnu-2.1.0.zip -o "$work/bws.zip"
printf '%s  %s\n' ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860 "$work/bws.zip" | sha256sum --check
unzip -q "$work/bws.zip" -d "$work/bws"
install -m 0755 "$work/bws/bws" /usr/local/bin/bws
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
install -d -o deploy -g deploy -m 0700 /home/deploy/.ssh
install -o deploy -g deploy -m 0600 "$key" /home/deploy/.ssh/authorized_keys
install -d -m 0755 /opt/travel-scam
install -d -o deploy -g deploy -m 0750 /opt/travel-scam/releases
install -d -o 10001 -g 10001 -m 0755 /opt/travel-scam/models
install -d -m 0700 /etc/travel-scam /var/lib/travel-scam
install -m 0755 "$(dirname "$0")/push-release.sh" /usr/local/sbin/travel-scam-deploy
printf 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/travel-scam-deploy *\n' > /etc/sudoers.d/travel-scam-deploy
chmod 0440 /etc/sudoers.d/travel-scam-deploy
visudo -cf /etc/sudoers.d/travel-scam-deploy
