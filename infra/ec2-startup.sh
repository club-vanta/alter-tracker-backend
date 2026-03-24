#!/bin/bash
# scripts/bootstrap.sh - Amazon Linux 2023 Docker Setup

# Update system
dnf update -y

# Install Docker and Compose plugin
dnf install -y docker docker-compose-plugin git

# Start and enable Docker
systemctl enable --now docker

# Add the default user to the docker group
usermod -aG docker ec2-user

# Verify installation in logs
docker --version && docker compose version
