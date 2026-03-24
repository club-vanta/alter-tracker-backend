#!/bin/bash
# Bootstrap script — Amazon Linux 2023

# ── System update ─────────────────────────────────────────────────────────────
dnf update -y

# ── Docker ────────────────────────────────────────────────────────────────────
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

# Install Docker Compose plugin (not in AL2023 repos)
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

docker --version && docker compose version

# ── Disk usage alert cron job ─────────────────────────────────────────────────
# Runs every 10 minutes. If disk usage on / exceeds 80%, publishes an SNS alert.

cat <<'EOF' > /usr/local/bin/disk-check.sh
#!/bin/bash
USAGE=$(df / | awk 'NR==2 {print $5}' | tr -d '%')
THRESHOLD=80
SNS_TOPIC_ARN="${sns_topic_arn}"

if [ "$USAGE" -gt "$THRESHOLD" ]; then
  aws sns publish \
    --region us-east-1 \
    --topic-arn "$SNS_TOPIC_ARN" \
    --subject "Disk alert: alter-tracker server" \
    --message "Disk usage is at $${USAGE}% on the alter-tracker server. Free up space before it fills up."
fi
EOF

chmod +x /usr/local/bin/disk-check.sh

dnf install -y cronie
systemctl enable --now crond
echo "*/10 * * * * root /usr/local/bin/disk-check.sh" > /etc/cron.d/disk-check

# ── Application files ─────────────────────────────────────────────────────────
# Terraform injects the repo's deploy.sh and docker-compose.yml as base64 so
# they are copied verbatim without any shell or templatefile escaping issues.

echo "${deploy_sh_b64}"      | base64 -d > /home/ec2-user/deploy.sh
echo "${docker_compose_b64}" | base64 -d > /home/ec2-user/docker-compose.yml

chmod +x /home/ec2-user/deploy.sh
chown ec2-user:ec2-user /home/ec2-user/deploy.sh /home/ec2-user/docker-compose.yml

# ── Initial deploy ─────────────────────────────────────────────────────────────
sh /home/ec2-user/deploy.sh
