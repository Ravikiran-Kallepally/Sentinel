# Infrastructure-as-code SKETCH for running Sentinel on AWS. Not applied by this project - it shows the shape
# of the production resources so you can discuss them and extend it. `terraform validate` should pass;
# `terraform plan` needs AWS credentials.
#
# Mapping from local stand-ins to managed services:
#   var/lake (Parquet)          -> S3 bucket (Delta/Iceberg tables)          aws_s3_bucket.lake
#   model registry (files)      -> S3 prefix + MLflow on ECS, or SageMaker Model Registry
#   ops.sqlite                  -> RDS PostgreSQL (+ pgvector for the RAG index)
#   in-process cache            -> ElastiCache Redis
#   container image             -> ECR                                       aws_ecr_repository.sentinel
#   API keys / LLM keys         -> Secrets Manager                           aws_secretsmanager_secret.llm
#   Kubernetes                  -> EKS (use the terraform-aws-modules/eks module)

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "env" {
  type    = string
  default = "dev"
}

provider "aws" {
  region = var.region
}

locals {
  name = "sentinel-${var.env}"
  tags = { project = "sentinel", env = var.env, data_classification = "confidential" }
}

resource "aws_s3_bucket" "lake" {
  bucket = "${local.name}-lake"
  tags   = local.tags
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_ecr_repository" "sentinel" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  tags = local.tags
}

resource "aws_secretsmanager_secret" "llm" {
  name        = "${local.name}/llm-api-keys"
  description = "LLM provider API keys for Sentinel (populate out of band, never in Terraform state)"
  tags        = local.tags
}

output "lake_bucket" { value = aws_s3_bucket.lake.bucket }
output "ecr_repository_url" { value = aws_ecr_repository.sentinel.repository_url }
