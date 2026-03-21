---
description: Pipeline de Deploy Multi-Região para Omni-Trader
---

Este workflow automatiza o build das imagens Docker e provisionamento da infraestrutura AWS via Terraform.

### 1. Build & Push de Microsserviços
// turbo
1. Executar o build das imagens Docker:
   `docker build -t omni-trader-us ./services/` (para us-east-1)
   `docker build -t omni-trader-jp ./services/` (para ap-northeast-1)

2. Fazer push para o AWS ECR:
   `aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin [ACCOUNT_ID].dkr.ecr.us-east-1.amazonaws.com`
   `docker tag omni-trader-us:latest [ACCOUNT_ID].dkr.ecr.us-east-1.amazonaws.com/omni-trader-us:latest`
   `docker push [ACCOUNT_ID].dkr.ecr.us-east-1.amazonaws.com/omni-trader-us:latest`

### 2. Infraestrutura (IaC) - Planejamento
1. Inicializar e validar as configurações regionalmente:
   `cd terraform/us-east-1 && terraform init && terraform validate && terraform plan -out=plan.tfplan`
   `cd terraform/ap-northeast-1 && terraform init && terraform validate && terraform plan -out=plan.tfplan`

### 3. Deploy Final (Requer Aprovação)
> [!IMPORTANT]
> O comando `terraform apply` está na Denylist do agente. O usuário deve autorizar manualmente no console após revisar os artefatos de `plan`.

1. Após aprovação visual:
   `terraform apply "plan.tfplan"` (em ambas as sub-pastas)
