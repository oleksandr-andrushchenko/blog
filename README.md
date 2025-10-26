# Blog

## Prerequisites

- AWS account
- AWS CLI installed and configured
    - You should have these files: `~/.aws/credentials` and `~/.aws/config`

## Available commands

```
  clean                Remove build artifacts
  delete-cert-infra    Delete cert CF stack
  delete-code-infra    Delete code CF stack
  delete-infra         Delete CF stack
  deploy-cert-infra    Deploy ACM certificate for the domain
  deploy-code-files    Zip and upload Lambda code to S3
  deploy-code-infra    Deploy S3 bucket for Lambda / CloudFront code
  deploy-infra         Deploy CF stack for the site
  deploy-site-files    Sync local site files to S3
  down                 Stop local Docker containers
  generate-code-files  Build Lambda zips for all listed LAMBDAS
  generate-site-files  Run content generator inside Docker container
  get-cert-arn         Fetch the ACM Certificate ARN and save to .env
  get-cert-infra       Show cert CF stack events
  get-code-infra       Show code CF stack events
  get-contact-form-function-url Fetch Lambda function URL and save to .env
  get-infra            Show CF stack events
  help                 Show this help
  invalidate           Invalidate CloudFront cache for the site
  login                Open shell in Docker container
  login-scripts        Open shell in scripts Docker container
  logs                 Show logs of Docker container
  open                 Show local site URL
  rebuild              Rebuild and start Docker containers
  up                   Start local Docker containers
```

## TODO

- optimize Projections for DynamoDB indexes
- optimize DynamoDB attributes
- add user tags, filter users by tags (?)

## Links

- chatgpt - https://chatgpt.com/c/68a63567-ab48-832d-8d2e-ff1e01da03d7