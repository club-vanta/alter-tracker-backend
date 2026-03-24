# ── Disk usage alerts ─────────────────────────────────────────────────────────

resource "aws_sns_topic" "infra_alerts" {
  name = "${local.project_name}-infra-alerts"
}

resource "aws_sns_topic_subscription" "infra_alerts_email" {
  topic_arn = aws_sns_topic.infra_alerts.arn
  protocol  = "email"
  endpoint  = "infra-alerts@${var.cloudflare_domain}"
}

# Allow the EC2 instance to publish to this topic
resource "aws_iam_role_policy" "sns_publish" {
  name = "sns-publish-infra-alerts"
  role = aws_iam_role.ec2_ssm_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = aws_sns_topic.infra_alerts.arn
    }]
  })
}

output "infra_alerts_topic_arn" {
  description = "SNS topic ARN — used by the disk usage cron script on the instance"
  value       = aws_sns_topic.infra_alerts.arn
}
