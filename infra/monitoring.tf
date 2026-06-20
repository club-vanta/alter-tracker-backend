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

# ── Application log alerts ─────────────────────────────────────────────────────

# ── EC2 resource alerts ────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "${local.project_name}-cpu-high"
  alarm_description   = "CPU usage above 80% for 10 minutes"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = aws_instance.app_server.id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.infra_alerts.arn]
  ok_actions    = [aws_sns_topic.infra_alerts.arn]
}

# ── Application log alerts ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_metric_filter" "log_error" {
  name           = "${local.project_name}-log-error"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "{ $.level = \"error\" }"

  metric_transformation {
    name      = "LogErrorCount"
    namespace = "${local.project_name}/App"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "log_error" {
  alarm_name          = "${local.project_name}-log-error"
  alarm_description   = "One or more error-level log entries in the last 5 minutes"
  namespace           = "${local.project_name}/App"
  metric_name         = "LogErrorCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.infra_alerts.arn]
  ok_actions    = [aws_sns_topic.infra_alerts.arn]
}

resource "aws_cloudwatch_log_metric_filter" "log_warning" {
  name           = "${local.project_name}-log-warning"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "{ $.level = \"warning\" }"

  metric_transformation {
    name      = "LogWarningCount"
    namespace = "${local.project_name}/App"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "log_warning" {
  alarm_name          = "${local.project_name}-log-warning"
  alarm_description   = "10 or more warning-level log entries in the last 10 minutes"
  namespace           = "${local.project_name}/App"
  metric_name         = "LogWarningCount"
  statistic           = "Sum"
  period              = 600
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.infra_alerts.arn]
  ok_actions    = [aws_sns_topic.infra_alerts.arn]
}
