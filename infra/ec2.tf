# This finds the latest Amazon Linux 2025 AMI automatically
data "aws_ami" "latest_al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "app_server" {
  ami           = data.aws_ami.latest_al2023.id
  instance_type = "t4g.small"

  subnet_id                   = module.vpc.public_subnets[0]
  vpc_security_group_ids      = [aws_security_group.app_sg.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_profile.name
  associate_public_ip_address = true
  ipv6_address_count          = 1

  # 1. Enforce IMDSv2 and set Hop Limit to 2 for Docker
  # Setting http_put_response_hop_limit = 2 is the "magic fix" for Docker. 
  # Without this, the containers would be blocked from fetching IAM credentials because the Docker bridge counts as a network hop.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # Forces IMDSv2
    http_put_response_hop_limit = 2          # Allows Docker containers to reach metadata
    instance_metadata_tags      = "enabled"
  }

  # 2. Source the external bootstrap script
  user_data = templatefile("${path.module}/ec2-startup.sh", {
    sns_topic_arn      = aws_sns_topic.infra_alerts.arn
    deploy_sh_b64      = base64encode(file("${path.module}/../scripts/deploy.sh"))
    docker_compose_b64 = base64encode(file("${path.module}/../docker-compose.yml"))
  })

  tags = {
    Name = "${local.project_name}-server"
  }
}

resource "aws_security_group" "app_sg" {
  name   = "${local.project_name}-sg"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
}

