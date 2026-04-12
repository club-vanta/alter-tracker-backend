resource "aws_cloudwatch_log_group" "app" {
  name              = "/${local.project_name}"
  retention_in_days = 30
}

resource "aws_iam_role_policy" "cloudwatch_logs" {
  name = "${local.project_name}-cloudwatch-logs"
  role = aws_iam_role.ec2_ssm_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "${aws_cloudwatch_log_group.app.arn}:*"
    }]
  })
}
