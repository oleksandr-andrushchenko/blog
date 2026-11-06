.DEFAULT_GOAL := help

LOCAL_ENV_FILE := .env

# Local Docker/tooling values always come from .env.
PROJECT_NAME := $(shell sed -n "s/^PROJECT_NAME=//p" $(LOCAL_ENV_FILE) 2>/dev/null)
WEB_LAMBDA_PORT := $(shell sed -n "s/^WEB_LAMBDA_PORT=//p" $(LOCAL_ENV_FILE) 2>/dev/null)
API_LAMBDA_PORT := $(shell sed -n "s/^API_LAMBDA_PORT=//p" $(LOCAL_ENV_FILE) 2>/dev/null)
DYNAMODB_PORT := $(shell sed -n "s/^DYNAMODB_PORT=//p" $(LOCAL_ENV_FILE) 2>/dev/null)
LOCAL_AWS_REGION := $(shell sed -n "s/^AWS_REGION=//p" $(LOCAL_ENV_FILE) 2>/dev/null)
LOCAL_AWS_REGION := $(if $(LOCAL_AWS_REGION),$(LOCAL_AWS_REGION),us-west-2)

# Detect docker compose command.
ifeq (, $(shell command -v docker-compose 2>/dev/null))
    ifeq (, $(shell command -v docker 2>/dev/null))
        $(error "Docker is not installed")
    endif
    DC := docker compose
else
    DC := docker-compose
endif

TEST_ENV_FILE := $(if $(wildcard .env.test.local),.env.test.local,.env.test)
export TEST_ENV_FILE
TEST_WEB_LAMBDA_PORT := $(shell sed -n "s/^WEB_LAMBDA_PORT=//p" $(TEST_ENV_FILE))
TEST_API_LAMBDA_PORT := $(shell sed -n "s/^API_LAMBDA_PORT=//p" $(TEST_ENV_FILE))
TEST_DYNAMODB_PORT := $(shell sed -n "s/^DYNAMODB_PORT=//p" $(TEST_ENV_FILE))
TEST_PROJECT_NAME := $(shell sed -n "s/^PROJECT_NAME=//p" $(TEST_ENV_FILE))
TEST_DC = WEB_LAMBDA_PORT=$(TEST_WEB_LAMBDA_PORT) API_LAMBDA_PORT=$(TEST_API_LAMBDA_PORT) DYNAMODB_PORT=$(TEST_DYNAMODB_PORT) $(DC) --env-file $(TEST_ENV_FILE) -f docker-compose.test.yml -p $(if $(TEST_PROJECT_NAME),$(if $(filter %-tests,$(TEST_PROJECT_NAME)),$(TEST_PROJECT_NAME),$(TEST_PROJECT_NAME)-tests),$(if $(PROJECT_NAME),$(PROJECT_NAME)-tests,blog-tests))
SCRIPTS_DC = $(DC) -f docker-compose.yml -f docker-compose.scripts.yml
TESTS_CONTAINER = tests
WEB_LAMBDA_CONTAINER = web-lambda
SCRIPTS_CONTAINER = scripts

API_LAMBDA_PORT ?= 5002

# Worktree .env.prod links point at the main checkout. Resolve before sed -i,
# which would otherwise replace the link with an independent configuration file.
PROD_ENV_FILE ?= .env.prod
override PROD_ENV_FILE := $(shell realpath -m -- "$(PROD_ENV_FILE)")
export PROD_ENV_FILE
export PROD_CONFIG_DIR := $(dir $(PROD_ENV_FILE))
export PROD_CONFIG_FILENAME := $(notdir $(PROD_ENV_FILE))

# AWS-backed deployments inherit a single snapshot resolved under the production lock.
# Legacy files remain supported only until the explicit migration is applied.
ifeq ($(PROD_CONFIG_LOADED),1)
AWS_OWNER := $(value AWS_OWNER)
APP_STAGE := $(value APP_STAGE)
APP_ENV := $(value APP_ENV)
APP_DEBUG := $(value APP_DEBUG)
DOMAIN_NAME := $(value DOMAIN_NAME)
HOSTED_ZONE_ID := $(value HOSTED_ZONE_ID)
TELEGRAM_CHAT_ID := $(value TELEGRAM_CHAT_ID)
TELEGRAM_LOG_LEVEL := $(value TELEGRAM_LOG_LEVEL)
NOTIFICATION_EMAIL := $(value NOTIFICATION_EMAIL)
NOTIFICATION_PHONE := $(value NOTIFICATION_PHONE)
GOOGLE_ANALYTICS_ID := $(value GOOGLE_ANALYTICS_ID)
INDEXNOW_KEY := $(value INDEXNOW_KEY)
GOOGLE_SEARCH_CONSOLE_SITE_URL := $(value GOOGLE_SEARCH_CONSOLE_SITE_URL)
YANDEX_WEBMASTER_USER_ID := $(value YANDEX_WEBMASTER_USER_ID)
YANDEX_WEBMASTER_HOST_ID := $(value YANDEX_WEBMASTER_HOST_ID)
GOOGLE_OAUTH_CLIENT_ID := $(value GOOGLE_OAUTH_CLIENT_ID)
APPLE_CREDENTIALS_SECRET_ARN := $(value APPLE_CREDENTIALS_SECRET_ARN)
TINYMCE_API_KEY := $(value TINYMCE_API_KEY)
THREADS_APP_ID := $(value THREADS_APP_ID)
FACEBOOK_CREDENTIALS_SECRET_ARN := $(value FACEBOOK_CREDENTIALS_SECRET_ARN)
LINKEDIN_APP_ID := $(value LINKEDIN_APP_ID)
LINKEDIN_API_VERSION := $(value LINKEDIN_API_VERSION)
else
AWS_OWNER := $(shell sed -n "s/^AWS_OWNER=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
APP_STAGE := $(shell sed -n "s/^APP_STAGE=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
APP_ENV := $(shell sed -n "s/^APP_ENV=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
APP_DEBUG := $(shell sed -n "s/^APP_DEBUG=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
DOMAIN_NAME := $(shell sed -n "s/^DOMAIN_NAME=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
HOSTED_ZONE_ID := $(shell sed -n "s/^HOSTED_ZONE_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
TELEGRAM_CHAT_ID := $(shell sed -n "s/^TELEGRAM_CHAT_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
TELEGRAM_LOG_LEVEL := $(shell sed -n "s/^TELEGRAM_LOG_LEVEL=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
NOTIFICATION_EMAIL := $(shell sed -n "s/^NOTIFICATION_EMAIL=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
NOTIFICATION_PHONE := $(shell sed -n "s/^NOTIFICATION_PHONE=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
GOOGLE_ANALYTICS_ID := $(shell sed -n "s/^GOOGLE_ANALYTICS_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
INDEXNOW_KEY := $(shell sed -n "s/^INDEXNOW_KEY=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
GOOGLE_SEARCH_CONSOLE_SITE_URL := $(shell sed -n "s/^GOOGLE_SEARCH_CONSOLE_SITE_URL=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
YANDEX_WEBMASTER_USER_ID := $(shell sed -n "s/^YANDEX_WEBMASTER_USER_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
YANDEX_WEBMASTER_HOST_ID := $(shell sed -n "s/^YANDEX_WEBMASTER_HOST_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
GOOGLE_OAUTH_CLIENT_ID := $(shell sed -n "s/^GOOGLE_OAUTH_CLIENT_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
APPLE_CREDENTIALS_SECRET_ARN := $(shell sed -n "s/^APPLE_CREDENTIALS_SECRET_ARN=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
TINYMCE_API_KEY := $(shell sed -n "s/^TINYMCE_API_KEY=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
THREADS_APP_ID := $(shell sed -n "s/^THREADS_APP_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
FACEBOOK_CREDENTIALS_SECRET_ARN := $(shell sed -n "s/^FACEBOOK_CREDENTIALS_SECRET_ARN=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
LINKEDIN_APP_ID := $(shell sed -n "s/^LINKEDIN_APP_ID=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
LINKEDIN_API_VERSION := $(or $(shell sed -n "s/^LINKEDIN_API_VERSION=//p" "$(PROD_ENV_FILE)" 2>/dev/null),202610)
endif
AWS_STACK := $(shell sed -n "s/^AWS_STACK=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
AWS_PROJECT := $(shell sed -n "s/^AWS_PROJECT=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
AWS_REGION := $(shell sed -n "s/^AWS_REGION=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
CSS_CACHE_COUNTER := $(shell sed -n "s/^CSS_CACHE_COUNTER=//p" "$(PROD_ENV_FILE)" 2>/dev/null)
JS_CACHE_COUNTER := $(shell sed -n "s/^JS_CACHE_COUNTER=//p" "$(PROD_ENV_FILE)" 2>/dev/null)

PRODUCTION_CONFIG_SCRIPT := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))scripts/production_config.py
CODE_STACK_NAME = $(AWS_STACK)-code
CERT_STACK_NAME = $(AWS_STACK)-cert
SITE_BUILD_DIR = .site-build
CODE_BUILD_DIR = .code-build
HOST_UID := $(shell id -u)
HOST_GID := $(shell id -g)

# Acquire the shared production lock before a fresh recursive Make reads settings.
# Local services/tests keep their independent worktree configuration and ports.
_PRODUCTION_LOCK_GOALS := $(filter deploy deployment deploy-% generate-code-files generate-%-lambda-code-files delete-% drop-cdn-cache,$(MAKECMDGOALS))
ifneq ($(_PRODUCTION_LOCK_GOALS),)
ifneq ($(PRODUCTION_LOCK_HELD),1)
_PRODUCTION_LOCK_REQUIRED := 1
endif
endif

ifeq ($(_PRODUCTION_LOCK_REQUIRED),1)
.PHONY: __production_locked $(MAKECMDGOALS)
$(MAKECMDGOALS): __production_locked ;
__production_locked:
	@python3 scripts/production_lock.py "$(PROD_ENV_FILE)" -- $(MAKE) -j1 PRODUCTION_LOCK_HELD=1 $(MAKECMDGOALS)
else

.PHONY: help
help: ## Show this help
	@echo "Available commands:"
	@awk -F '## ' '/^[a-zA-Z0-9_-]+:.*##/ { \
		split($$1, a, ":"); \
		printf "  \033[36m%-20s\033[0m %s\n", a[1], $$2 \
	}' $(MAKEFILE_LIST) | sort

.PHONY: check-prod-env-file
check-prod-env-file:
	@if [ ! -f "$(PROD_ENV_FILE)" ]; then \
		echo "❌ Missing $(PROD_ENV_FILE). See .env.prod.example and docs/production-configuration.md."; \
		exit 1; \
	fi

.PHONY: check-env
check-env: check-prod-env-file
	@if [ -z "$(AWS_STACK)" ] || [ -z "$(AWS_PROJECT)" ] || [ -z "$(AWS_REGION)" ]; then \
		echo "❌ Missing required AWS environment variables in $(PROD_ENV_FILE)."; \
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
		--region us-east-1

.PHONY: delete-cert-infra
delete-cert-infra: check-env check-aws ## Delete cert CF stack
	aws cloudformation delete-stack \
		--stack-name $(CERT_STACK_NAME) \
		--region us-east-1 \
		--profile $(AWS_PROJECT)
	@echo "🧼 Waiting for stack to be fully deleted..."
	aws cloudformation wait stack-delete-complete \
		--stack-name $(CERT_STACK_NAME) \
		--region us-east-1 \
		--profile $(AWS_PROJECT)
	@echo "✅ Stack $(CERT_STACK_NAME) deleted."

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
	@CERTIFICATE_ARN=$$(aws cloudformation describe-stacks \
		--stack-name $(CERT_STACK_NAME) \
		--region us-east-1 \
		--profile $(AWS_PROJECT) \
		--query "Stacks[0].Outputs[?OutputKey=='CertificateArn'].OutputValue | [0]" \
		--output text) || exit $$?; \
	case "$$CERTIFICATE_ARN" in \
		arn:*:acm:us-east-1:*:certificate/*) ;; \
		*) echo "❌ CloudFront certificate ARN not found. Run make deploy-cert-infra first."; exit 1 ;; \
	esac; \
	WEB_CERTIFICATE_ARN=$$(aws cloudformation describe-stacks \
		--stack-name $(CERT_STACK_NAME) \
		--region us-east-1 \
		--profile $(AWS_PROJECT) \
		--query "Stacks[0].Outputs[?OutputKey=='WebCertificateArn'].OutputValue | [0]" \
		--output text) || exit $$?; \
	case "$$WEB_CERTIFICATE_ARN" in \
		arn:*:acm:us-east-1:*:certificate/*) ;; \
		*) echo "❌ Web CloudFront certificate ARN not found. Run make deploy-cert-infra first."; exit 1 ;; \
	esac; \
	python3 "$(PRODUCTION_CONFIG_SCRIPT)" deploy \
		--certificate-arn "$$CERTIFICATE_ARN" \
		--web-certificate-arn "$$WEB_CERTIFICATE_ARN"
	@echo "📤 Stack outputs:"
	@aws cloudformation describe-stacks \
		--stack-name $(AWS_STACK) \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION) \
		--query "Stacks[0].Outputs[].[OutputKey, OutputValue]" \
		--output text \
		--no-cli-pager

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
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION)
	@echo "✅ Lambda code uploaded successfully"

.PHONY: deploy-web-lambda
deploy-web-lambda: check-env check-aws generate-web-lambda-code-files ## Build, upload, and deploy only the Web Lambda
	aws s3 cp $(CODE_BUILD_DIR)/web-function-$$(sed -n 's/^WEB_LAMBDA_CODE_TIMESTAMP=//p' "$(PROD_ENV_FILE)").zip s3://$(CODE_STACK_NAME)/web-function-$$(sed -n 's/^WEB_LAMBDA_CODE_TIMESTAMP=//p' "$(PROD_ENV_FILE)").zip --profile $(AWS_PROJECT) --region $(AWS_REGION)
	$(MAKE) deploy-infra

.PHONY: deploy-api-lambda
deploy-api-lambda: check-env check-aws generate-api-lambda-code-files ## Build, upload, and deploy only the API Lambda
	aws s3 cp $(CODE_BUILD_DIR)/api-function-$$(sed -n 's/^API_LAMBDA_CODE_TIMESTAMP=//p' "$(PROD_ENV_FILE)").zip s3://$(CODE_STACK_NAME)/api-function-$$(sed -n 's/^API_LAMBDA_CODE_TIMESTAMP=//p' "$(PROD_ENV_FILE)").zip --profile $(AWS_PROJECT) --region $(AWS_REGION)
	$(MAKE) deploy-infra

.PHONY: deploy-img-lambda
deploy-img-lambda: check-env check-aws generate-img-lambda-code-files ## Build, upload, and deploy only the Image Lambda
	aws s3 cp $(CODE_BUILD_DIR)/img-function-$$(sed -n 's/^IMG_LAMBDA_CODE_TIMESTAMP=//p' "$(PROD_ENV_FILE)").zip s3://$(CODE_STACK_NAME)/img-function-$$(sed -n 's/^IMG_LAMBDA_CODE_TIMESTAMP=//p' "$(PROD_ENV_FILE)").zip --profile $(AWS_PROJECT) --region $(AWS_REGION)
	$(MAKE) deploy-infra

.PHONY: deploy-site-files
deploy-site-files: check-env check-aws generate-site-files ## Sync local site files to S3
	@echo "📤 Uploading Site files to s3://$(AWS_STACK)-site..."
	aws s3 sync ./$(SITE_BUILD_DIR) s3://$(AWS_STACK)-site \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION)
	@echo "✅ Site files uploaded successfully"

.PHONY: drop-cdn-cache
drop-cdn-cache: check-env check-aws ## Invalidate CloudFront cache for the site
	@echo "🔎 Finding CloudFront distribution for static.$(DOMAIN_NAME)..."
	@DISTRIBUTION_ID=$$(aws cloudfront list-distributions \
		--profile $(AWS_PROJECT) \
		--region $(AWS_REGION) \
		--query "DistributionList.Items[?Aliases.Items[?contains(@, 'static.$(DOMAIN_NAME)')]].Id" \
		--output text); \
	if [ -n "$$DISTRIBUTION_ID" ]; then \
		echo "⚡ Invalidating CloudFront cache for distribution $$DISTRIBUTION_ID..."; \
		aws cloudfront create-invalidation \
			--profile $(AWS_PROJECT) \
			--region $(AWS_REGION) \
			--distribution-id "$$DISTRIBUTION_ID" \
			--paths "/*"; \
	else \
		echo "⚠️  CloudFront distribution not found for static.$(DOMAIN_NAME) — skipping invalidation."; \
	fi

.PHONY: generate-site-files
generate-site-files: scripts-up ## Run content generator inside Docker container
	@echo "📦 Generating Site files..."
	mkdir -p $(SITE_BUILD_DIR)
	rm -rf $(SITE_BUILD_DIR)/*
	$(SCRIPTS_DC) exec $(SCRIPTS_CONTAINER) python3 scripts/generate_site_build.py
	@echo "✅ Site files saved to $(SITE_BUILD_DIR) successfully"

.PHONY: generate-web-lambda-code-files
generate-web-lambda-code-files: check-prod-env-file scripts-up ## Build the Web Lambda ZIP
	@echo "📦 Generating Web Lambda code files..."
	rm -rf .tmp/web
	rm -f $(CODE_BUILD_DIR)/web-function*.zip
	mkdir -p .tmp/web $(CODE_BUILD_DIR)
	$(SCRIPTS_DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) pip install --no-cache-dir -r /app/web-lambda/requirements.txt -t /app/.tmp/web
	$(SCRIPTS_DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) python3 /app/scripts/generate_lambda_build.py web
	@TIMESTAMP=$$(date +%Y%m%d%H%M%S); mv $(CODE_BUILD_DIR)/web-function.zip $(CODE_BUILD_DIR)/web-function-$$TIMESTAMP.zip; if grep -q "^WEB_LAMBDA_CODE_TIMESTAMP=" "$(PROD_ENV_FILE)"; then sed -i.bak "s|^WEB_LAMBDA_CODE_TIMESTAMP=.*|WEB_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP|" "$(PROD_ENV_FILE)"; rm -f "$(PROD_ENV_FILE)".bak; else printf "\nWEB_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP\n" >> "$(PROD_ENV_FILE)"; fi

.PHONY: generate-api-lambda-code-files
generate-api-lambda-code-files: check-prod-env-file scripts-up ## Build the API Lambda ZIP
	@echo "📦 Generating API Lambda code files..."
	rm -rf .tmp/api
	rm -f $(CODE_BUILD_DIR)/api-function*.zip
	mkdir -p .tmp/api $(CODE_BUILD_DIR)
	$(SCRIPTS_DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) pip install --no-cache-dir -r /app/api-lambda/requirements.txt -t /app/.tmp/api
	$(SCRIPTS_DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) python3 /app/scripts/generate_lambda_build.py api
	@TIMESTAMP=$$(date +%Y%m%d%H%M%S); mv $(CODE_BUILD_DIR)/api-function.zip $(CODE_BUILD_DIR)/api-function-$$TIMESTAMP.zip; if grep -q "^API_LAMBDA_CODE_TIMESTAMP=" "$(PROD_ENV_FILE)"; then sed -i.bak "s|^API_LAMBDA_CODE_TIMESTAMP=.*|API_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP|" "$(PROD_ENV_FILE)"; rm -f "$(PROD_ENV_FILE)".bak; else printf "\nAPI_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP\n" >> "$(PROD_ENV_FILE)"; fi

.PHONY: generate-img-lambda-code-files
generate-img-lambda-code-files: check-prod-env-file scripts-up ## Build the Image Lambda ZIP
	@echo "📦 Generating Image Lambda code files..."
	rm -rf .tmp/img
	rm -f $(CODE_BUILD_DIR)/img-function*.zip
	mkdir -p .tmp/img $(CODE_BUILD_DIR)
	$(SCRIPTS_DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) pip install --no-cache-dir -r /app/img-lambda/requirements.txt -t /app/.tmp/img
	$(SCRIPTS_DC) exec --user $(HOST_UID):$(HOST_GID) $(SCRIPTS_CONTAINER) python3 /app/scripts/generate_img_lambda_build.py
	@TIMESTAMP=$$(date +%Y%m%d%H%M%S); mv $(CODE_BUILD_DIR)/img-function.zip $(CODE_BUILD_DIR)/img-function-$$TIMESTAMP.zip; if grep -q "^IMG_LAMBDA_CODE_TIMESTAMP=" "$(PROD_ENV_FILE)"; then sed -i.bak "s|^IMG_LAMBDA_CODE_TIMESTAMP=.*|IMG_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP|" "$(PROD_ENV_FILE)"; rm -f "$(PROD_ENV_FILE)".bak; else printf "\nIMG_LAMBDA_CODE_TIMESTAMP=$$TIMESTAMP\n" >> "$(PROD_ENV_FILE)"; fi

.PHONY: generate-code-files
generate-code-files: ## Build all Lambda ZIPs
	@echo "📦 Generating all Lambda code files..."
	rm -rf $(CODE_BUILD_DIR) .tmp
	mkdir -p $(CODE_BUILD_DIR)
	$(MAKE) generate-web-lambda-code-files
	$(MAKE) generate-api-lambda-code-files
	$(MAKE) generate-img-lambda-code-files

.PHONY: aws-login
aws-login: check-env check-aws ## Obtain AWS auth token
	aws login --profile $(AWS_PROJECT)

.PHONY: deployment
deployment: deploy ## Alias for a full deployment

.PHONY: deploy
deploy: check-env check-aws ## Deploy certificates, code bucket, Lambdas, application, and static files
	$(MAKE) deploy-cert-infra
	$(MAKE) deploy-code-infra
	$(MAKE) deploy-code-files
	$(MAKE) deploy-infra
	$(MAKE) deploy-site-files

.PHONY: up
up: ## Start local Docker containers
	$(DC) up -d --remove-orphans

.PHONY: down
down: ## Stop local Docker containers
	$(SCRIPTS_DC) down

.PHONY: restart
restart: down up ## Restart local Docker containers

.PHONY: rebuild
rebuild: ## Rebuild and start Docker containers
	$(DC) up -d --build --force-recreate

.PHONY: scripts-up
scripts-up: ## Start the scripts container and local DynamoDB
	$(SCRIPTS_DC) up -d --build dynamodb scripts

.PHONY: login
login: ## Open shell in Docker container
	$(DC) exec -it $(WEB_LAMBDA_CONTAINER) bash

.PHONY: login-scripts
login-scripts: scripts-up ## Open shell in scripts Docker container
	$(SCRIPTS_DC) exec -it $(SCRIPTS_CONTAINER) bash

.PHONY: logs
logs: ## Show logs of Docker containers
	$(DC) logs -f

.PHONY: open
open: ## Show local site URL
	@echo "🌐 Visit http://localhost:$(WEB_LAMBDA_PORT) in your browser manually."

.PHONY: create-local-dynamodb
create-local-dynamodb: scripts-up ## Create local DynamoDB table
	@echo "🚀 Creating local DynamoDB table app..."
	@if aws dynamodb describe-table \
	    --profile dummy \
	    --region $(LOCAL_AWS_REGION) \
		--table-name app \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" > /dev/null 2>&1; then \
		echo "⚠️ Table app already exists, skipping creation."; \
	else \
		echo "🧩 Extracting DynamoDB schema from CloudFormation..."; \
		$(SCRIPTS_DC) exec $(SCRIPTS_CONTAINER) python3 scripts/extract_dynamodb_schema.py > /tmp/dynamodb_schema.json; \
		if [ ! -s /tmp/dynamodb_schema.json ]; then echo '❌ Failed to generate valid DynamoDB schema JSON'; exit 1; fi; \
		echo "📄 Generated schema:"; \
		cat /tmp/dynamodb_schema.json | jq .; \
		aws dynamodb create-table \
		    --profile dummy \
			--region $(LOCAL_AWS_REGION) \
			--cli-input-json file:///tmp/dynamodb_schema.json \
			--table-name app \
			--endpoint-url http://localhost:$(DYNAMODB_PORT) \
			--no-cli-pager || exit $$?; \
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
		--region $(LOCAL_AWS_REGION) \
		--no-cli-pager \
		--output json

.PHONY: drop-local-dynamodb
drop-local-dynamodb: ## Drop DynamoDB table in local DynamoDB
	@echo "🗑️ Dropping local DynamoDB table app..."
	@if aws dynamodb describe-table \
		--profile dummy \
		--region $(LOCAL_AWS_REGION) \
		--table-name app \
		--endpoint-url "http://localhost:$(DYNAMODB_PORT)" > /dev/null 2>&1; then \
		aws dynamodb delete-table \
		    --profile dummy \
		    --region $(LOCAL_AWS_REGION) \
			--table-name app \
			--endpoint-url http://localhost:$(DYNAMODB_PORT) \
			--no-cli-pager || exit $$?; \
		echo "✅ Table app deleted from local DynamoDB"; \
	else \
		echo "⚠️ Table app does not exist, skipping deletion."; \
	fi

.PHONY: create-local-dynamodb-dummy-fixtures
create-local-dynamodb-dummy-fixtures: scripts-up ## Populate local DynamoDB with dummy data
	@echo "📦 Populating local DynamoDB table app with dummy data..."
	$(SCRIPTS_DC) exec $(SCRIPTS_CONTAINER) python3 scripts/generate_dummy_fixtures.py

.PHONY: recreate-local-dynamodb
recreate-local-dynamodb: ## Recreate DynamoDB table in local DynamoDB & populate dummy data
	$(MAKE) drop-local-dynamodb
	$(MAKE) create-local-dynamodb
	$(MAKE) create-local-dynamodb-dummy-fixtures

.PHONY: tests
tests: ## Run the full test suite in the isolated Docker Compose stack
	@status=0; \
	echo "==> Starting test services..."; \
	$(TEST_DC) up -d --build --remove-orphans || status=$$?; \
	if [ $$status -eq 0 ]; then \
		echo "==> Running pytest..."; \
		$(TEST_DC) exec $(TESTS_CONTAINER) python3 -m pytest -o log_cli_level=INFO -o log_cli=true -v /tests -s || status=$$?; \
	fi; \
	echo "==> Stopping test services..."; \
	$(TEST_DC) down || true; \
	exit $$status

.PHONY: tail-scripts-logs
tail-scripts-logs: scripts-up ## Tail scripts logs
	$(SCRIPTS_DC) logs -f $(SCRIPTS_CONTAINER)

endif # shared production lock wrapper
