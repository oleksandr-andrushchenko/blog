# Load .env into Makefile environment
include .env
export

# Detect docker compose command
ifeq (, $(shell command -v docker-compose 2>/dev/null))
    ifeq (, $(shell command -v docker 2>/dev/null))
        $(error "Docker is not installed")
    endif
    DC := docker compose
else
    DC := docker-compose
endif

TEST_WEB_LAMBDA_PORT := $(shell sed -n "s/^WEB_LAMBDA_PORT=//p" .env.test)
TEST_API_LAMBDA_PORT := $(shell sed -n "s/^API_LAMBDA_PORT=//p" .env.test)
TEST_DYNAMODB_PORT := $(shell sed -n "s/^DYNAMODB_PORT=//p" .env.test)
TEST_DC = WEB_LAMBDA_PORT=$(TEST_WEB_LAMBDA_PORT) API_LAMBDA_PORT=$(TEST_API_LAMBDA_PORT) DYNAMODB_PORT=$(TEST_DYNAMODB_PORT) $(DC) --env-file .env.test -f docker-compose.test.yml -p $(if $(PROJECT_NAME),$(PROJECT_NAME)-tests,blog-tests)
TESTS_CONTAINER = tests

WEB_LAMBDA_CONTAINER = web-lambda
SCRIPTS_CONTAINER = scripts
CODE_STACK_NAME = $(AWS_STACK)-code
CERT_STACK_NAME = $(AWS_STACK)-cert
SITE_BUILD_DIR=.site-build
CODE_BUILD_DIR=.code-build

HOST_UID := $(shell id -u)
HOST_GID := $(shell id -g)
API_LAMBDA_PORT ?= 5002

.PHONY: help
help: ## Show this help
	@echo "Available commands:"
	@awk -F '## ' '/^[a-zA-Z0-9_-]+:.*##/ { \
		split($$1, a, ":"); \
		printf "  \033[36m%-20s\033[0m %s\n", a[1], $$2 \
	}' $(MAKEFILE_LIST) | sort

.PHONY: check-env
check-env:
	@if [ -z $(AWS_STACK) ] || [ -z $(AWS_PROJECT) ] || [ -z $(AWS_REGION) ]; then \
		echo "❌ Missing required environment variables. Did you run 'cp .env.example .env' and fill it?"; \
		exit 1; \
	fi

.PHONY: check-aws
check-aws:
	@command -v aws >/dev/null 2>&1 || { echo "❌ AWS CLI not found"; exit 1; }

.PHONY: clean
clean: ## Remove build artifacts
	@rm -rf $(SITE_BUILD_DIR) $(CODE_BUILD_DIR) .tmp
	@echo "🧹 Cleaned build artifacts"

.PHONY: deploy-cert-infra
deploy-cert-infra: check-env check-aws ## Deploy ACM certificate for the domain
	@echo "🔐 Deploying ACM certificate for $(DOMAIN_NAME) in us-east-1..."
	aws cloudformation deploy \
		--profile $(AWS_PROJECT) \
		--region us-east-1 \
		--template-file cf-cert.yml \
		--stack-name $(CERT_STACK_NAME) \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--parameter-overrides \
			DomainName="$(DOMAIN_NAME)" \
			HostedZoneId="$(HOSTED_ZONE_ID)" \
			Project="$(AWS_PROJECT)" \
			Owner="$(AWS_OWNER)" \
			Stage="$(APP_STAGE)" \
		--tags \
			Project="$(AWS_PROJECT)" \
			Owner="$(AWS_OWNER)" \
			Stage="$(APP_STAGE)" \
			Region="us-east-1"
	@echo "✅ Certificate deployment triggered. Waiting for DNS validation..."

.PHONY: get-cert-infra
get-cert-infra: check-env check-aws ## Show cert CF stack events
	aws cloudformation describe-stack-events \
		--stack-name $(CERT_STACK_NAME) \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION)

.PHONY: delete-cert-infra
delete-cert-infra: check-env check-aws ## Delete cert CF stack
	aws cloudformation delete-stack \
		--stack-name $(CERT_STACK_NAME) \
		--region $(AWS_REGION) \
		--profile $(AWS_PROJECT)
	@echo "🧼 Waiting for stack to be fully deleted..."
	aws cloudformation wait stack-delete-complete \
		--stack-name $(CERT_STACK_NAME) \
		--region $(AWS_REGION) \
		--profile $(AWS_PROJECT)
	@echo "✅ Stack $(CERT_STACK_NAME) deleted."

.PHONY: get-cert-arn
get-cert-arn: check-env check-aws ## Fetch the ACM Certificate ARN and save to .env
	@echo "🔍 Fetching ACM Certificate ARN for $(DOMAIN_NAME) in us-east-1..."
	@ARN=$$(aws cloudformation describe-stacks \
		--stack-name $(CERT_STACK_NAME) \
		--region us-east-1 \
		--profile $(AWS_PROJECT) \
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
			echo "\nCLOUDFRONT_CERTIFICATE_ARN=$$ARN" >> .env; \
		fi; \
		echo "📝 Updated .env with CLOUDFRONT_CERTIFICATE_ARN"; \
	fi

.PHONY: deploy-code-infra
deploy-code-infra: check-env check-aws ## Deploy S3 bucket for Lambda / CloudFront code
	@echo "📦 Deploying code bucket for $(AWS_STACK)..."
	aws cloudformation deploy \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION) \
		--template-file cf-code.yml \
		--stack-name $(CODE_STACK_NAME) \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--parameter-overrides \
			Project="$(AWS_PROJECT)" \
			Owner="$(AWS_OWNER)" \
			Stage="$(APP_STAGE)" \
		--tags \
			Project="$(AWS_PROJECT)" \
			Owner="$(AWS_OWNER)" \
			Stage="$(APP_STAGE)" \
			Region="$(AWS_REGION)"
	@echo "✅ Code bucket deployment triggered."

.PHONY: get-code-infra
get-code-infra: check-env check-aws ## Show code CF stack events
	aws cloudformation describe-stack-events \
		--stack-name $(CODE_STACK_NAME) \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION)

.PHONY: delete-code-infra
delete-code-infra: check-env check-aws ## Delete code CF stack
	aws cloudformation delete-stack \
		--stack-name $(CODE_STACK_NAME) \
		--region $(AWS_REGION) \
		--profile $(AWS_PROJECT)
	@echo "🧼 Waiting for stack to be fully deleted..."
	aws cloudformation wait stack-delete-complete \
		--stack-name $(CODE_STACK_NAME) \
		--region $(AWS_REGION) \
		--profile $(AWS_PROJECT)
	@echo "✅ Stack $(CODE_STACK_NAME) deleted."

.PHONY: deploy-infra
deploy-infra: check-env check-aws ## Deploy CF stack for the site
	@echo "🚀 Deploying CloudFormation stack for $(DOMAIN_NAME)..."
	@if [ -z "$(CLOUDFRONT_CERTIFICATE_ARN)" ]; then \
		echo "❌ CLOUDFRONT_CERTIFICATE_ARN is not defined. Run \`make get-cert-arn\` or export it in .env"; \
		exit 1; \
	fi
	aws cloudformation deploy \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION) \
		--template-file cf.yml \
		--stack-name $(AWS_STACK) \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--parameter-overrides \
			Project="$(AWS_PROJECT)" \
			Owner="$(AWS_OWNER)" \
			Stage="$(APP_STAGE)" \
			Env="$(APP_ENV)" \
			Debug="$(APP_DEBUG)" \
			Secret="$(APP_SECRET)" \
			DomainName="$(DOMAIN_NAME)" \
			HostedZoneId="$(HOSTED_ZONE_ID)" \
			CertificateArn="$(CLOUDFRONT_CERTIFICATE_ARN)" \
			NotificationEmail="$(NOTIFICATION_EMAIL)" \
			NotificationPhone="$(NOTIFICATION_PHONE)" \
			GoogleAnalyticsId="$(GOOGLE_ANALYTICS_ID)" \
			GoogleOauthClientId="$(GOOGLE_OAUTH_CLIENT_ID)" \
			GoogleOauthClientSecret="$(GOOGLE_OAUTH_CLIENT_SECRET)" \
			TinyMceApiKey="$(TINYMCE_API_KEY)" \
			CssCacheCounter="$(CSS_CACHE_COUNTER)" \
			JsCacheCounter="$(JS_CACHE_COUNTER)" \
			AuthJwtSecret="$(AUTH_JWT_SECRET)" \
			WebFuncS3Key="web-function-$$(sed -n 's/^WEB_LAMBDA_CODE_TIMESTAMP=//p' .env).zip" \
			ApiFuncS3Key="api-function-$$(sed -n 's/^API_LAMBDA_CODE_TIMESTAMP=//p' .env).zip" \
			ImgFuncS3Key="img-function-$$(sed -n 's/^IMG_LAMBDA_CODE_TIMESTAMP=//p' .env).zip" \
		--tags \
			Project="$(AWS_PROJECT)" \
			Owner="$(AWS_OWNER)" \
			Stage="$(APP_STAGE)" \
			Region="$(AWS_REGION)"
	@echo "📤 Stack outputs:"
	@aws cloudformation describe-stacks \
		--stack-name $(AWS_STACK) \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION) \
		--query "Stacks[0].Outputs" \
		--output table


.PHONY: get-infra
get-infra: check-env check-aws ## Show CF stack events
	aws cloudformation describe-stack-events \
		--stack-name $(AWS_STACK) \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION)

.PHONY: delete-infra
delete-infra: check-env check-aws ## Delete CF stack
	aws cloudformation delete-stack \
		--stack-name $(AWS_STACK) \
		--region $(AWS_REGION) \
		--profile $(AWS_PROJECT)
	@echo "🧼 Waiting for stack to be fully deleted..."
	aws cloudformation wait stack-delete-complete \
		--stack-name $(AWS_STACK) \
		--region $(AWS_REGION) \
		--profile $(AWS_PROJECT)
	@echo "✅ Stack $(AWS_STACK) deleted."

.PHONY: deploy-code-files
deploy-code-files: check-env check-aws generate-code-files ## Zip and upload Lambda code to S3
	@echo "📤 Uploading Lambda code to s3://$(CODE_STACK_NAME)..."
	aws s3 sync ./$(CODE_BUILD_DIR) s3://$(CODE_STACK_NAME) \
		--delete \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION)
	@echo "✅ Lambda code uploaded successfully"

.PHONY: deploy-web-lambda
deploy-web-lambda: check-env check-aws generate-web-lambda-code-files ## Build, upload, and deploy only the Web Lambda
	aws s3 cp $(CODE_BUILD_DIR)/web-function-$$(sed -n 's/^WEB_LAMBDA_CODE_TIMESTAMP=//p' .env).zip s3://$(CODE_STACK_NAME)/web-function-$$(sed -n 's/^WEB_LAMBDA_CODE_TIMESTAMP=//p' .env).zip --profile $(AWS_PROJECT) --region $(AWS_REGION)
	$(MAKE) --no-print-directory deploy-infra

.PHONY: deploy-api-lambda
deploy-api-lambda: check-env check-aws generate-api-lambda-code-files ## Build, upload, and deploy only the API Lambda
	aws s3 cp $(CODE_BUILD_DIR)/api-function-$$(sed -n 's/^API_LAMBDA_CODE_TIMESTAMP=//p' .env).zip s3://$(CODE_STACK_NAME)/api-function-$$(sed -n 's/^API_LAMBDA_CODE_TIMESTAMP=//p' .env).zip --profile $(AWS_PROJECT) --region $(AWS_REGION)
	$(MAKE) --no-print-directory deploy-infra

.PHONY: deploy-img-lambda
deploy-img-lambda: check-env check-aws generate-img-lambda-code-files ## Build, upload, and deploy only the Image Lambda
	aws s3 cp $(CODE_BUILD_DIR)/img-function-$$(sed -n 's/^IMG_LAMBDA_CODE_TIMESTAMP=//p' .env).zip s3://$(CODE_STACK_NAME)/img-function-$$(sed -n 's/^IMG_LAMBDA_CODE_TIMESTAMP=//p' .env).zip --profile $(AWS_PROJECT) --region $(AWS_REGION)
	$(MAKE) --no-print-directory deploy-infra

.PHONY: deploy-site-files
deploy-site-files: check-env check-aws generate-site-files ## Sync local site files to S3
	@echo "📤 Uploading Site files to s3://$(AWS_STACK)-site..."
	aws s3 sync ./$(SITE_BUILD_DIR) s3://$(AWS_STACK)-site \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION)
	@echo "✅ Site files uploaded successfully"

.PHONY: drop-cdn-cache
drop-cdn-cache: check-env check-aws ## Invalidate CloudFront cache for the site
	@echo "🔎 Finding CloudFront distribution for $(DOMAIN_NAME)..."
	@DISTRIBUTION_ID=$$(aws cloudfront list-distributions \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION) \
		--query "DistributionList.Items[?Aliases.Items[?contains(@, '$(DOMAIN_NAME)')]].Id" \
		--output text); \
	if [ -n "$$DISTRIBUTION_ID" ]; then \
		echo "⚡ Invalidating CloudFront cache for distribution $$DISTRIBUTION_ID..."; \
		aws cloudfront create-invalidation \
			--profile $(AWS_PROJECT) \
			--region $(AWS_REGION) \
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

.PHONY: restart
restart: down up ## Restart local Docker containers

.PHONY: rebuild
rebuild: ## Rebuild and start Docker containers
	$(DC) up -d --build --force-recreate

.PHONY: login
login: ## Open shell in Docker container
	$(DC) exec -it $(WEB_LAMBDA_CONTAINER) bash

login-scripts: ## Open shell in scripts Docker container
	$(DC) exec -it $(SCRIPTS_CONTAINER) bash

.PHONY: logs
logs: ## Show logs of Docker container
	$(DC) logs -f $(WEB_LAMBDA_CONTAINER)

.PHONY: generate-site-files
generate-site-files: ## Run content generator inside Docker container
	@echo "📦 Generating Site files..."
	mkdir -p $(SITE_BUILD_DIR)
	rm -rf $(SITE_BUILD_DIR)/*
	$(DC) exec $(SCRIPTS_CONTAINER) python3 scripts/generate_site_build.py
	@echo "✅ Site files saved to $(SITE_BUILD_DIR) successfully"

.PHONY: generate-web-lambda-code-files
generate-web-lambda-code-files: ## Build the Web Lambda ZIP
	@echo "📦 Generating Web Lambda code files..."
	rm -rf .tmp/web
	rm -f $(CODE_BUILD_DIR)/web-function*.zip
	mkdir -p .tmp/web $(CODE_BUILD_DIR)
	$(DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) pip install --no-cache-dir -r /app/web-lambda/requirements.txt -t /app/.tmp/web
	$(DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) python3 /app/scripts/generate_lambda_build.py web
	@TIMESTAMP=$$(date +%Y%m%d%H%M%S); mv $(CODE_BUILD_DIR)/web-function.zip $(CODE_BUILD_DIR)/web-function-$$TIMESTAMP.zip; if grep -q "^WEB_LAMBDA_CODE_TIMESTAMP=" .env; then sed -i.bak "s|^WEB_LAMBDA_CODE_TIMESTAMP=.*|WEB_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP|" .env; rm -f .env.bak; else printf "\nWEB_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP\n" >> .env; fi

.PHONY: generate-api-lambda-code-files
generate-api-lambda-code-files: ## Build the API Lambda ZIP
	@echo "📦 Generating API Lambda code files..."
	rm -rf .tmp/api
	rm -f $(CODE_BUILD_DIR)/api-function*.zip
	mkdir -p .tmp/api $(CODE_BUILD_DIR)
	$(DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) pip install --no-cache-dir -r /app/api-lambda/requirements.txt -t /app/.tmp/api
	$(DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) python3 /app/scripts/generate_lambda_build.py api
	@TIMESTAMP=$$(date +%Y%m%d%H%M%S); mv $(CODE_BUILD_DIR)/api-function.zip $(CODE_BUILD_DIR)/api-function-$$TIMESTAMP.zip; if grep -q "^API_LAMBDA_CODE_TIMESTAMP=" .env; then sed -i.bak "s|^API_LAMBDA_CODE_TIMESTAMP=.*|API_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP|" .env; rm -f .env.bak; else printf "\nAPI_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP\n" >> .env; fi

.PHONY: generate-img-lambda-code-files
generate-img-lambda-code-files: ## Build the Image Lambda ZIP
	@echo "📦 Generating Image Lambda code files..."
	rm -rf .tmp/img
	rm -f $(CODE_BUILD_DIR)/img-function*.zip
	mkdir -p .tmp/img $(CODE_BUILD_DIR)
	$(DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) pip install --no-cache-dir -r /app/img-lambda/requirements.txt -t /app/.tmp/img
	$(DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) python3 /app/scripts/generate_img_lambda_build.py
	@TIMESTAMP=$$(date +%Y%m%d%H%M%S); mv $(CODE_BUILD_DIR)/img-function.zip $(CODE_BUILD_DIR)/img-function-$$TIMESTAMP.zip; if grep -q "^IMG_LAMBDA_CODE_TIMESTAMP=" .env; then sed -i.bak "s|^IMG_LAMBDA_CODE_TIMESTAMP=.*|IMG_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP|" .env; rm -f .env.bak; else printf "\nIMG_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP\n" >> .env; fi

.PHONY: generate-code-files
generate-code-files: ## Build all Lambda ZIPs
	@echo "📦 Generating all Lambda code files..."
	rm -rf $(CODE_BUILD_DIR) .tmp
	mkdir -p $(CODE_BUILD_DIR)
	$(MAKE) --no-print-directory generate-web-lambda-code-files
	$(MAKE) --no-print-directory generate-api-lambda-code-files
	$(MAKE) --no-print-directory generate-img-lambda-code-files

.PHONY: open
open: ## Show local site URL
	@echo "🌐 Visit http://localhost:$(WEB_LAMBDA_PORT) in your browser manually."

.PHONY: aws-login
aws-login: ## Obtain AWS auth token
	aws login --profile $(AWS_PROJECT)

.PHONY: create-local-dynamodb
create-local-dynamodb: ## Create local DynamoDB table
	@echo "🚀 Creating local DynamoDB table app..."
	@if aws dynamodb describe-table \
	    --profile dummy \
		--region $(AWS_REGION) \
		--table-name app \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" > /dev/null 2>&1; then \
		echo "⚠️ Table app already exists, skipping creation."; \
	else \
		echo "🧩 Extracting DynamoDB schema from CloudFormation..."; \
		$(DC) exec $(SCRIPTS_CONTAINER) python3 scripts/extract_dynamodb_schema.py > /tmp/dynamodb_schema.json; \
		if [ ! -s /tmp/dynamodb_schema.json ]; then echo '❌ Failed to generate valid DynamoDB schema JSON'; exit 1; fi; \
		echo "📄 Generated schema:"; \
		cat /tmp/dynamodb_schema.json | jq .; \
		aws dynamodb create-table \
		    --profile dummy \
			--region $(AWS_REGION) \
			--cli-input-json file:///tmp/dynamodb_schema.json \
			--table-name app \
			--endpoint-url http://localhost:$(DYNAMODB_PORT) \
			--no-cli-pager; \
		rm -f /tmp/dynamodb_schema.json; \
		echo "✅ DynamoDB table app initialized in local DynamoDB"; \
	fi

.PHONY: fetch-local-dynamodb
fetch-local-dynamodb: ## Fetch 100 records from local DynamoDB
	@echo "📦 Fetching 100 records from app..."
	aws dynamodb scan \
	    --profile dummy \
		--table-name app \
		--limit 100 \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" \
		--region $(AWS_REGION) \
		--no-cli-pager \
		--output json

.PHONY: drop-local-dynamodb
drop-local-dynamodb: ## Drop DynamoDB table in local DynamoDB
	@echo "🗑️ Dropping local DynamoDB table app..."
	@if aws dynamodb describe-table \
		--profile dummy \
		--region $(AWS_REGION) \
		--table-name app \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" > /dev/null 2>&1; then \
		aws dynamodb delete-table \
		    --profile dummy \
		    --region $(AWS_REGION) \
			--table-name app \
			--endpoint-url http://localhost:$(DYNAMODB_PORT) \
			--no-cli-pager; \
		echo "✅ Table app deleted from local DynamoDB"; \
	else \
		echo "⚠️ Table app does not exist, skipping deletion."; \
	fi

.PHONY: create-local-dynamodb-dummy-fixtures
create-local-dynamodb-dummy-fixtures: ## Populate local DynamoDB with dummy data
	@echo "📦 Populating local DynamoDB table app with dummy data..."
	curl -sf -XPOST "http://localhost:$(API_LAMBDA_PORT)/dummy-fixtures"

.PHONY: recreate-local-dynamodb
recreate-local-dynamodb: drop-local-dynamodb create-local-dynamodb create-local-dynamodb-dummy-fixtures ## Recreate DynamoDB table in local DynamoDB & populate dummy data

.PHONY: tests
tests: ## Run tests in the isolated test Docker Compose stack and remove it afterward
	@status=0; \
	$(TEST_DC) up -d --build --remove-orphans || status=$$?; \
	if [ $$status -eq 0 ]; then \
		$(TEST_DC) exec $(TESTS_CONTAINER) python3 -m pytest -o log_cli_level=INFO -o log_cli=true -v /tests/test_web_api.py -m "functional" -v -s || status=$$?; \
	fi; \
	$(TEST_DC) down || true; \
	exit $$status




.PHONY: tail-scripts-logs
tail-scripts-logs: ## Tail scripts logs
	$(DC) logs -f $(SCRIPTS_CONTAINER)

.PHONY: deploy
deploy: aws-login restart deploy-site-files deploy-code-files deploy-infra ## Deploy static and code files