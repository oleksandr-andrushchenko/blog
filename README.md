# Blog

## Prerequisites

- docker & docker compose have to be installed
- AWS account
- AWS CLI installed and configured
    - You should have these files: `~/.aws/credentials` and `~/.aws/config`

## Available commands

```
  aws-login            Obtain AWS auth token
  clean                Remove build artifacts
  create-local-dynamodb Create local DynamoDB table
  create-local-dynamodb-dummy-fixtures Populate local DynamoDB with dummy data
  delete-cert-infra    Delete cert CF stack
  delete-code-infra    Delete code CF stack
  delete-infra         Delete CF stack
  deploy-cert-infra    Deploy ACM certificate for the domain
  deploy-code-files    Zip and upload Lambda code to S3
  deploy-web-lambda    Build, upload, and deploy only the Web Lambda
  deploy-api-lambda    Build, upload, and deploy only the API Lambda
  deploy-img-lambda    Build, upload, and deploy only the Image Lambda
  deploy-code-infra    Deploy S3 bucket for Lambda / CloudFront code
  deploy-infra         Deploy CF stack for the site
  deploy-site-files    Sync local site files to S3
  down                 Stop local Docker containers
  drop-local-dynamodb  Drop DynamoDB table in local DynamoDB
  fetch-local-dynamodb Fetch 100 records from local DynamoDB
  generate-code-files  Build all Lambda zips
  generate-web-lambda-code-files  Build the Web Lambda zip
  generate-api-lambda-code-files  Build the API Lambda zip
  generate-img-lambda-code-files  Build the Image Lambda zip
  # Individual deploy-* targets also upload the selected artifact and update only that Lambda
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
  restart              Restart local Docker containers
  tail-scripts-logs    Tail scripts logs
  tail-test-logs       Tail test logs
  up                   Start local Docker containers
```

## Lambda layout

- `lambda-shared/` — shared backend code and templates
- `web-lambda/` — website Lambda handler and dependencies
- `api-lambda/` — API Lambda
- `img-lambda/` — S3 image variant Lambda

## TODO

- optimize Projections for DynamoDB indexes
- optimize DynamoDB attributes
- map app's endpoints to api gateway
- delete public images func?
- add users email/sms notifications (post published/liked/disliked, user followed/blocked etc.)
- add meta info for tags and images (created_by, created_at)
- add aria attributes (+allow them in tinymce)
- add footer tag for post/articles, put related articles (Like "Futher reading", based on tags)
- replace env secrets with secrets manager storage (CS becomes slower)
- jpeg images have problems with dimensions determination (on uploads)
- add image watermarks
- add author to the footer
- update logo in google auth
- posts page: add popular tags to "filter by tags" block
- improve post comments
- posts form: submit slugs URL version (instead of queries)
- generate tag combos for post pages (article's tag combos for crawlers)
- remove personal contact details
- add user_name and user_slug attributes to articles, render user in article fragments, sync when user changed
- tinymce: on image change - call api to drop the old image
- content on article edit page is not editable
- cover all the avaiable web/API endpoints with integrations tests

## Links

- favicon - https://realfavicongenerator.net