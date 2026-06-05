# AWS Bedrock 用の最小IAM（Terraform）。`terraform init && terraform apply` で適用。
# セキュリティ: アクセスキーは TF state に残さない方針。本番はロール(キーレス)を推奨。

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name_prefix" {
  type    = string
  default = "aiia"
}

# Bedrock(Claude) 呼び出しの最小権限ポリシー（../iam-bedrock-policy.json を再利用）
resource "aws_iam_policy" "bedrock_invoke" {
  name   = "${var.name_prefix}-bedrock-invoke"
  policy = file("${path.module}/../iam-bedrock-policy.json")
}

# 推奨(キーレス): Lambda/ECS が assume するアプリ実行ロール
resource "aws_iam_role" "app" {
  name = "${var.name_prefix}-app-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = ["lambda.amazonaws.com", "ecs-tasks.amazonaws.com"] },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "app_bedrock" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.bedrock_invoke.arn
}

output "policy_arn" {
  value = aws_iam_policy.bedrock_invoke.arn
}

output "app_role_arn" {
  value = aws_iam_role.app.arn
}

# --- 開発用にIAMユーザーが必要な場合（任意・コメントアウト） ---
# resource "aws_iam_user" "dev" {
#   name = "${var.name_prefix}-dev"
# }
# resource "aws_iam_user_policy_attachment" "dev_bedrock" {
#   user       = aws_iam_user.dev.name
#   policy_arn = aws_iam_policy.bedrock_invoke.arn
# }
# 注: アクセスキーは `aws iam create-access-key --user-name aiia-dev` で発行し、
#     環境設定のシークレットに登録（TF state に残さない）。
