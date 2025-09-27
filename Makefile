# Load .env into Makefile environment
include .env
export

DC = docker-compose
BE_FUNCTION_CONTAINER = $(DOCKER_NAME)-be-function
SCRIPTS_CONTAINER = $(DOCKER_NAME)-scripts
CODE_STACK_NAME = $(STACK_NAME)-code
CERT_STACK_NAME = $(STACK_NAME)-cert
SITE_BUILD_DIR=.site-build
CODE_BUILD_DIR=.code-build
LAMBDAS = be-function

.PHONY: help
help: ## Show this help
	@echo "Available commands:"
	@awk -F '## ' '/^[a-zA-Z0-9_-]+:.*##/ { \
		split($$1, a, ":"); \
		printf "  \033[36m%-20s\033[0m %s\n", a[1], $$2 \
	}' $(MAKEFILE_LIST) | sort

.PHONY: check-env
check-env:
	@if [ -z "$(STACK_NAME)" ] || [ -z "$(AWS_PROFILE_NAME)" ] || [ -z "$(AWS_REGION)" ]; then \
		echo "❌ Missing required environment variables. Did you run 'cp .env.example .env' and fill it?"; \
		exit 1; \
	fi

.PHONY: check-aws
check-aws:
	@command -v aws >/dev/null 2>&1 || { echo "❌ AWS CLI not found"; exit 1; }

.PHONY: clean
clean: ## Remove build artifacts
	@rm -rf $(SITE_BUILD_DIR) $(CODE_BUILD_DIR)
	@echo "🧹 Cleaned build artifacts"

.PHONY: deploy-cert-infra
deploy-cert-infra: check-env check-aws ## Deploy ACM certificate for the domain
	@echo "🔐 Deploying ACM certificate for $(DOMAIN_NAME) in us-east-1..."
	aws cloudformation deploy \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "us-east-1" \
		--template-file cf-cert.yml \
		--stack-name "$(CERT_STACK_NAME)" \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--parameter-overrides \
			DomainName="$(DOMAIN_NAME)" \
			HostedZoneId="$(HOSTED_ZONE_ID)" \
			TagProject="$(TAG_PROJECT)" \
			TagOwner="$(TAG_OWNER)" \
			TagEnvironment="$(TAG_ENVIRONMENT)" \
			TagRegion="us-east-1" \
		--tags \
			Project="$(TAG_PROJECT)" \
			Owner="$(TAG_OWNER)" \
			Environment="$(TAG_ENVIRONMENT)" \
			Region="us-east-1"
	@echo "✅ Certificate deployment triggered. Waiting for DNS validation..."

.PHONY: get-cert-infra
get-cert-infra: check-env check-aws ## Show cert CF stack events
	aws cloudformation describe-stack-events \
		--stack-name "$(CERT_STACK_NAME)" \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)"

.PHONY: delete-cert-infra
delete-cert-infra: check-env check-aws ## Delete cert CF stack
	aws cloudformation delete-stack \
		--stack-name "$(CERT_STACK_NAME)" \
		--region "$(AWS_REGION)" \
		--profile "$(AWS_PROFILE_NAME)"
	@echo "🧼 Waiting for stack to be fully deleted..."
	aws cloudformation wait stack-delete-complete \
		--stack-name "$(CERT_STACK_NAME)" \
		--region "$(AWS_REGION)" \
		--profile "$(AWS_PROFILE_NAME)"
	@echo "✅ Stack $(CERT_STACK_NAME) deleted."

.PHONY: get-cert-arn
get-cert-arn: check-env check-aws ## Fetch the ACM Certificate ARN and save to .env
	@echo "🔍 Fetching ACM Certificate ARN for $(DOMAIN_NAME) in us-east-1..."
	@ARN=$$(aws cloudformation describe-stacks \
		--stack-name "$(CERT_STACK_NAME)" \
		--region "us-east-1" \
		--profile "$(AWS_PROFILE_NAME)" \
		--query "Stacks[0].Outputs[?OutputKey=='CertificateArn'].OutputValue" \
		--output text); \
	if [ -z "$$ARN" ]; then \
		echo "❌ Certificate ARN not found. Make sure the certificate stack was deployed successfully."; \
	else \
		echo "✅ Certificate ARN for $(DOMAIN_NAME): $$ARN"; \
		if grep -q "^CLOUDFRONT_CERTIFICATE_ARN=" .env; then \
			sed -i.bak "s|^CLOUDFRONT_CERTIFICATE_ARN=.*|CLOUDFRONT_CERTIFICATE_ARN=$$ARN|" .env; \
			rm -f .env.bak; \
		else \
			echo "CLOUDFRONT_CERTIFICATE_ARN=$$ARN" >> .env; \
		fi; \
		echo "📝 Updated .env with CLOUDFRONT_CERTIFICATE_ARN"; \
	fi

.PHONY: deploy-code-infra
deploy-code-infra: check-env check-aws ## Deploy S3 bucket for Lambda / CloudFront code
	@echo "📦 Deploying code bucket for $(STACK_NAME)..."
	aws cloudformation deploy \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)" \
		--template-file cf-code.yml \
		--stack-name "$(CODE_STACK_NAME)" \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--parameter-overrides \
			TagProject="$(TAG_PROJECT)" \
			TagOwner="$(TAG_OWNER)" \
			TagEnvironment="$(TAG_ENVIRONMENT)" \
			TagRegion="$(AWS_REGION)" \
		--tags \
			Project="$(TAG_PROJECT)" \
			Owner="$(TAG_OWNER)" \
			Environment="$(TAG_ENVIRONMENT)" \
			Region="$(AWS_REGION)"
	@echo "✅ Code bucket deployment triggered."

.PHONY: get-code-infra
get-code-infra: check-env check-aws ## Show code CF stack events
	aws cloudformation describe-stack-events \
		--stack-name "$(CODE_STACK_NAME)" \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)"

.PHONY: delete-code-infra
delete-code-infra: check-env check-aws ## Delete code CF stack
	aws cloudformation delete-stack \
		--stack-name "$(CODE_STACK_NAME)" \
		--region "$(AWS_REGION)" \
		--profile "$(AWS_PROFILE_NAME)"
	@echo "🧼 Waiting for stack to be fully deleted..."
	aws cloudformation wait stack-delete-complete \
		--stack-name "$(CODE_STACK_NAME)" \
		--region "$(AWS_REGION)" \
		--profile "$(AWS_PROFILE_NAME)"
	@echo "✅ Stack $(CODE_STACK_NAME) deleted."

.PHONY: deploy-infra
deploy-infra: check-env check-aws ## Deploy CF stack for the site
	@echo "🚀 Deploying CloudFormation stack for $(DOMAIN_NAME)..."
	@if [ -z "$(CLOUDFRONT_CERTIFICATE_ARN)" ]; then \
		echo "❌ CLOUDFRONT_CERTIFICATE_ARN is not defined. Run \`make get-cert-arn\` or export it in .env"; \
		exit 1; \
	fi
	aws cloudformation deploy \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)" \
		--template-file cf.yml \
		--stack-name "$(STACK_NAME)" \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--parameter-overrides \
			DomainName="$(DOMAIN_NAME)" \
			HostedZoneId="$(HOSTED_ZONE_ID)" \
			CertificateArn="$(CLOUDFRONT_CERTIFICATE_ARN)" \
			NotificationEmail="$(NOTIFICATION_EMAIL)" \
			NotificationPhone="$(NOTIFICATION_PHONE)" \
			TagProject="$(TAG_PROJECT)" \
			TagOwner="$(TAG_OWNER)" \
			TagEnvironment="$(TAG_ENVIRONMENT)" \
			TagRegion="$(AWS_REGION)" \
		--tags \
			Project="$(TAG_PROJECT)" \
			Owner="$(TAG_OWNER)" \
			Environment="$(TAG_ENVIRONMENT)"
	@echo "📤 Stack outputs:"
	@aws cloudformation describe-stacks \
		--stack-name "$(STACK_NAME)" \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)" \
		--query "Stacks[0].Outputs" \
		--output table

.PHONY: get-infra
get-infra: check-env check-aws ## Show CF stack events
	aws cloudformation describe-stack-events \
		--stack-name "$(STACK_NAME)" \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)"

.PHONY: delete-infra
delete-infra: check-env check-aws ## Delete CF stack
	aws cloudformation delete-stack \
		--stack-name "$(STACK_NAME)" \
		--region "$(AWS_REGION)" \
		--profile "$(AWS_PROFILE_NAME)"
	@echo "🧼 Waiting for stack to be fully deleted..."
	aws cloudformation wait stack-delete-complete \
		--stack-name "$(STACK_NAME)" \
		--region "$(AWS_REGION)" \
		--profile "$(AWS_PROFILE_NAME)"
	@echo "✅ Stack $(STACK_NAME) deleted."

.PHONY: get-contact-form-function-url
get-contact-form-function-url: check-env check-aws ## Fetch Lambda function URL and save to .env
	@echo "📡 Fetching Lambda Function URL..."
	@LAMBDA_URL=$$(aws cloudformation describe-stacks \
		--stack-name "$(STACK_NAME)" \
		--query "Stacks[0].Outputs[?OutputKey=='ContactFormEndpoint'].OutputValue" \
		--output text \
		--region "$(AWS_REGION)" \
		--profile "$(AWS_PROFILE)"); \
	if grep -q "^CONTACT_FORM_FUNCTION_URL=" .env; then \
		sed -i.bak "s|^CONTACT_FORM_FUNCTION_URL=.*|CONTACT_FORM_FUNCTION_URL=$$LAMBDA_URL|" .env; \
		rm -f .env.bak; \
	else \
		echo "\nCONTACT_FORM_FUNCTION_URL=$$LAMBDA_URL" >> .env; \
	fi; \
	echo "✅ Saved CONTACT_FORM_FUNCTION_URL=$$LAMBDA_URL to .env"

.PHONY: deploy-code-files
deploy-code-files: check-env check-aws generate-code-files ## Zip and upload Lambda code to S3
	@echo "📤 Uploading Lambda code to s3://$(CODE_STACK_NAME)..."
	aws s3 sync ./$(CODE_BUILD_DIR) s3://$(CODE_STACK_NAME) \
		--delete \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)"
	@echo "✅ Lambda code uploaded successfully"

.PHONY: deploy-site-files
deploy-site-files: check-env check-aws generate-site-files ## Sync local site files to S3
	@echo "📤 Uploading Site files to s3://$(STACK_NAME)-site..."
	aws s3 sync ./$(SITE_BUILD_DIR) s3://$(STACK_NAME)-site \
		--delete \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)"
	@echo "✅ Site files uploaded successfully"

.PHONY: invalidate
invalidate: check-env check-aws ## Invalidate CloudFront cache for the site
	@echo "🔎 Finding CloudFront distribution for $(DOMAIN_NAME)..."
	@DISTRIBUTION_ID=$$(aws cloudfront list-distributions \
		--profile "$(AWS_PROFILE_NAME)" \
		--region "$(AWS_REGION)" \
		--query "DistributionList.Items[?Aliases.Items[?contains(@, '$(DOMAIN_NAME)')]].Id" \
		--output text); \
	if [ -n "$$DISTRIBUTION_ID" ]; then \
		echo "⚡ Invalidating CloudFront cache for distribution $$DISTRIBUTION_ID..."; \
		aws cloudfront create-invalidation \
			--profile "$(AWS_PROFILE_NAME)" \
			--region "$(AWS_REGION)" \
			--distribution-id "$$DISTRIBUTION_ID" \
			--paths "/*"; \
	else \
		echo "⚠️  CloudFront distribution not found for $(DOMAIN_NAME) — skipping invalidation."; \
	fi

.PHONY: up
up: ## Start local Docker containers
	$(DC) up -d --remove-orphans

.PHONY: down
down: ## Stop local Docker containers
	$(DC) down

.PHONY: rebuild
rebuild: ## Rebuild and start Docker containers
	$(DC) up -d --build

.PHONY: login
login: ## Open shell in Docker container
	docker exec -it $(BE_FUNCTION_CONTAINER) bash

login-scripts: ## Open shell in scripts Docker container
	docker exec -it $(SCRIPTS_CONTAINER) bash

.PHONY: logs
logs: ## Show logs of Docker container
	docker logs -f $(BE_FUNCTION_CONTAINER)

.PHONY: generate-site-files
generate-site-files: ## Run content generator inside Docker container
	@echo "📦 Generating Site files..."
	mkdir -p $(SITE_BUILD_DIR)
	rm -rf $(SITE_BUILD_DIR)/*
	docker exec -it $(APP_CONTAINER) python generate.py
	@echo "✅ Site files saved to $(SITE_BUILD_DIR) successfully"

.PHONY: generate-code-files
generate-code-files: ## Build Lambda zips for all listed LAMBDAS
	@echo "📦 Building Lambda zips for: $(LAMBDAS)..."
	mkdir -p $(CODE_BUILD_DIR)
	rm -rf $(CODE_BUILD_DIR)/*

	@for lambda_name in $(LAMBDAS); do \
		echo "🛠 Building $$lambda_name..."; \
		LAMBDA_DIR="$$lambda_name-src"; \
		TMP_DIR="$(CODE_BUILD_DIR)/tmp_$$lambda_name"; \
		mkdir -p "$$TMP_DIR"; \
		cp -r "$$LAMBDA_DIR/." "$$TMP_DIR/"; \
		if [ -f "$$LAMBDA_DIR/requirements.txt" ]; then \
			pip install -r "$$LAMBDA_DIR/requirements.txt" -t "$$TMP_DIR"; \
		fi; \
		cd "$$TMP_DIR" && zip -r "../$$lambda_name.zip" . && cd - > /dev/null; \
		rm -rf "$$TMP_DIR"; \
	done

	@echo "✅ All Lambda zips created in $(CODE_BUILD_DIR)"

.PHONY: open
open: ## Show local site URL
	@echo "🌐 Visit http://localhost:$(BE_FUNCTION_PORT) in your browser manually."

.PHONY: create-local-dynamodb
create-local-dynamodb:
	@echo "🚀 Creating local DynamoDB table $(DYNAMODB_TABLE)..."
	@if AWS_ACCESS_KEY_ID=$(AWS_ACCESS_KEY_ID) AWS_SECRET_ACCESS_KEY=$(AWS_SECRET_ACCESS_KEY) \
		aws dynamodb describe-table \
		--region "$(AWS_REGION)" \
		--table-name "$(DYNAMODB_TABLE)" \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" > /dev/null 2>&1; then \
		echo "⚠️ Table $(DYNAMODB_TABLE) already exists, skipping creation."; \
	else \
		AWS_ACCESS_KEY_ID=$(AWS_ACCESS_KEY_ID) AWS_SECRET_ACCESS_KEY=$(AWS_SECRET_ACCESS_KEY) \
		aws dynamodb create-table \
			--region "$(AWS_REGION)" \
			--table-name "$(DYNAMODB_TABLE)" \
			--billing-mode PAY_PER_REQUEST \
			--attribute-definitions \
				AttributeName=pk,AttributeType=S \
				AttributeName=sk,AttributeType=S \
				AttributeName=gsi_provider_sub,AttributeType=S \
				AttributeName=gsi_email,AttributeType=S \
				AttributeName=gsi_post_status_pk,AttributeType=S \
				AttributeName=created_at,AttributeType=N \
				AttributeName=gsi_tag_pk,AttributeType=S \
				AttributeName=posts_count,AttributeType=N \
				AttributeName=gsi_tag_name_pk,AttributeType=S \
				AttributeName=tag_name,AttributeType=S \
				AttributeName=gsi_user_status_pk,AttributeType=S \
				AttributeName=gsi_post_user_status_pk,AttributeType=S \
			--key-schema \
				AttributeName=pk,KeyType=HASH \
				AttributeName=sk,KeyType=RANGE \
			--global-secondary-indexes file://dynamodb_gsis.json \
			--endpoint-url http://localhost:$(DYNAMODB_PORT) \
			--no-cli-pager; \
		echo "✅ DynamoDB table $(DYNAMODB_TABLE) initialized in local DynamoDB"; \
	fi

.PHONY: fetch-local-dynamodb
fetch-local-dynamodb: ## Fetch 100 records from local DynamoDB
	@echo "📦 Fetching 100 records from $(DYNAMODB_TABLE)..."
	AWS_ACCESS_KEY_ID=$(AWS_ACCESS_KEY_ID) AWS_SECRET_ACCESS_KEY=$(AWS_SECRET_ACCESS_KEY) \
	aws dynamodb scan \
		--table-name "$(DYNAMODB_TABLE)" \
		--limit 100 \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" \
		--region "$(AWS_REGION)" \
		--no-cli-pager \
		--output json

.PHONY: drop-local-dynamodb
drop-local-dynamodb: ## Drop DynamoDB table in local DynamoDB
	@echo "🗑️ Dropping local DynamoDB table $(DYNAMODB_TABLE)..."
	@if AWS_ACCESS_KEY_ID=$(AWS_ACCESS_KEY_ID) AWS_SECRET_ACCESS_KEY=$(AWS_SECRET_ACCESS_KEY) \
		aws dynamodb describe-table \
		--region "$(AWS_REGION)" \
		--table-name "$(DYNAMODB_TABLE)" \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" > /dev/null 2>&1; then \
		AWS_ACCESS_KEY_ID=$(AWS_ACCESS_KEY_ID) AWS_SECRET_ACCESS_KEY=$(AWS_SECRET_ACCESS_KEY) \
		aws dynamodb delete-table \
			--region "$(AWS_REGION)" \
			--table-name "$(DYNAMODB_TABLE)" \
			--endpoint-url http://localhost:$(DYNAMODB_PORT) \
			--no-cli-pager; \
		echo "✅ Table $(DYNAMODB_TABLE) deleted from local DynamoDB"; \
	else \
		echo "⚠️ Table $(DYNAMODB_TABLE) does not exist, skipping deletion."; \
	fi

.PHONY: create-local-dynamodb-dummy-fixtures
create-local-dynamodb-dummy-fixtures: ## Populate local DynamoDB with dummy data
	@echo "📦 Populating local DynamoDB table $(DYNAMODB_TABLE) with dummy data..."
	curl -sf -XPOST "http://localhost:$(BE_FUNCTION_PORT)/dummy-fixtures"

.PHONY: recreate-local-dynamodb
recreate-local-dynamodb: drop-local-dynamodb create-local-dynamodb create-local-dynamodb-dummy-fixtures ## Recreate DynamoDB table in local DynamoDB & populate dummy data