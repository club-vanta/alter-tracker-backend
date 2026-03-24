resource "aws_iam_role" "ec2_ssm_role" {
  name = "${local.project_name}-ssm-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_policy" {
  role       = aws_iam_role.ec2_ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "${local.project_name}-instance-profile"
  role = aws_iam_role.ec2_ssm_role.name
}

output "ssm_connect_command" {
  value = "aws ssm start-session --target ${aws_instance.app_server.id}"
}
