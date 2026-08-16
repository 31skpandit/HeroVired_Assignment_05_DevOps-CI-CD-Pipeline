#!/bin/bash
apt-get update -y
apt-get install -y docker.io unzip curl
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip -q awscliv2.zip
./aws/install
