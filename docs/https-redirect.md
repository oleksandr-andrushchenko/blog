# HTTP to HTTPS for the main website

The main domain uses a separate CloudFront distribution in front of the existing
regional API Gateway custom domain. HTTP page requests receive a 301 redirect
with their path and query string preserved. CloudFront connects to the regional
endpoint over HTTPS and forwards the viewer Host header, selecting the existing
custom-domain mapping and certificate. Using the regional endpoint as the origin
avoids a DNS loop after the public alias moves to CloudFront.

The managed CachingDisabled and AllViewer policies preserve cookies,
Authorization and query strings without caching pages. Error caching is also
disabled. The static distribution and API subdomain are independent of this
change. No Lambda ZIPs or application deployment are required.

## Deploying separately from other work

Do not run `make deploy-infra` in a checkout containing unrelated infrastructure
changes. That target deploys the entire local template and also reads local code
timestamps and other parameter values from `.env.prod`.

After an explicitly authorized commit containing only the HTTPS changes, use a
clean checkout of that commit. Alternatively, apply the task-only patch to an
isolated checkout of its recorded base commit. Neither approach needs to stash
or change the parallel chat's working files.

Before deploying, compare the isolated template with the currently deployed
CloudFormation template. If the base differs from production, apply only the
HTTPS infrastructure changes to the production baseline; do not deploy an older
baseline that would remove resources or roll back other changes.

1. Update the certificate stack in `us-east-1` using the isolated `cf-cert.yml`,
   keeping its current parameter values. This creates the main-domain certificate
   in `us-east-1` and exports `WebCertificateArn` while retaining the static
   certificate and its output. The regional main-domain certificate in the main
   stack remains in use by API Gateway. Review the change set before executing it.
2. Read the certificate stack's `WebCertificateArn` output. Prepare a main-stack
   change set with the isolated `cf.yml`, supplying that ARN as the new
   `WebCertificateArn` parameter and `UsePreviousValue: true` for every existing
   parameter. This preserves deployed Lambda ZIP keys, secrets and configuration
   even if local `.env.prod` timestamps have changed. Upload the template through
   the existing code bucket because it exceeds CloudFormation's inline limit.
3. Review the change set. Expected resource changes are adding
   `WebCloudFrontDistribution` and updating `DNSRecord`. Stop if it changes any
   Lambda function, adds unrelated features, removes resources, or replaces the
   existing API Gateway domain or mapping. Execute only after deployment is
   explicitly authorized.
4. After CloudFront and DNS finish updating, check HTTP `/` and a nested URL with
   query parameters without following redirects: expect 301 and the equivalent
   HTTPS Location. Check HTTPS pages, login/logout, cookies, error statuses and
   form submissions. Verify static HTTP redirects still work.

For normal future deployments from a checkout containing only intended changes,
run `make deploy-cert-infra` before `make deploy-infra`. The latter refuses to
deploy if the new certificate output is missing or invalid.

To roll back this routing change, point the main domain alias back to the
existing regional web gateway through a reviewed stack update. Keep the
distribution until the DNS change has propagated.
