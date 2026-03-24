#!/bin/bash
# Bootstrap script — Amazon Linux 2023

# ── System update ─────────────────────────────────────────────────────────────
dnf update -y

# ── Docker ────────────────────────────────────────────────────────────────────
dnf install -y docker docker-compose-plugin git

systemctl enable --now docker
usermod -aG docker ec2-user

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

echo "*/10 * * * * root /usr/local/bin/disk-check.sh" > /etc/cron.d/disk-check
