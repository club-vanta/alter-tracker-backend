module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6"

  name = "${local.project_name}-vpc"
  cidr = "10.0.0.0/16"

  azs            = ["us-east-1a"]
  public_subnets = ["10.0.1.0/24"]

  enable_nat_gateway = false # Keeping it cheap/free tier
  enable_vpn_gateway = false
}



