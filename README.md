# Blog

## Prerequisites

- AWS account
- AWS CLI installed and configured
    - You should have these files: `~/.aws/credentials` and `~/.aws/config`

## Available commands

```
  clean                Remove build artifacts
  create-local-dynamodb-dummy-fixtures Populate local DynamoDB with dummy data
  delete-cert-infra    Delete cert CF stack
  delete-code-infra    Delete code CF stack
  delete-infra         Delete CF stack
  deploy-cert-infra    Deploy ACM certificate for the domain
  deploy-code-files    Zip and upload Lambda code to S3
  deploy-code-infra    Deploy S3 bucket for Lambda / CloudFront code
  deploy-infra         Deploy CF stack for the site
  deploy-site-files    Sync local site files to S3
  down                 Stop local Docker containers
  drop-local-dynamodb  Drop DynamoDB table in local DynamoDB
  fetch-local-dynamodb Fetch 100 records from local DynamoDB
  generate-code-files  Build Lambda zip for be-function
  generate-site-files  Run content generator inside Docker container
  get-cert-arn         Fetch the ACM Certificate ARN and save to .env
  get-cert-infra       Show cert CF stack events
  get-code-infra       Show code CF stack events
  get-infra            Show CF stack events
  help                 Show this help
  invalidate           Invalidate CloudFront cache for the site
  login                Open shell in Docker container
  login-scripts        Open shell in scripts Docker container
  logs                 Show logs of Docker container
  open                 Show local site URL
  rebuild              Rebuild and start Docker containers
  recreate-local-dynamodb Recreate DynamoDB table in local DynamoDB & populate dummy data
  tail-scripts-logs    Tail scripts logs
  tail-test-logs       Tail test logs
  up                   Start local Docker containers
```

## TODO

- optimize Projections for DynamoDB indexes
- optimize DynamoDB attributes
- add post comments (simple)
- map app's endpoints to api gateway
- delete public images func?x`
- add users email/sms notifications (post published/liked/disliked, user followed/blocked etc.)
- add meta info for tags and images (created_by, created_at)
- add SEO meta tags (image, other property/name metas)
- implement instant lambda deployments
- add redirects from UUid page versions to seo versions
- increase auth token lifetime
- add aria attributes
- add footer tag for post/articles, put related articles (Like "Futher reading", based on tags)
## Links

- favicon - https://realfavicongenerator.net