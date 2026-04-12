# EC2 On-Demand Start/Stop Design

**Date:** 2026-04-12
**Status:** Approved

## Goal

Start and stop the EC2 instance on demand via GitHub Actions manual workflows, saving compute costs when the app is not in use. On start, Cloudflare DNS is updated automatically with the new public IP via `tofu apply`.

## Trigger

Two `workflow_dispatch` workflows in GitHub Actions — triggerable from the GitHub UI or GitHub mobile app.

## Workflows

### `.github/workflows/start-instance.yml`

1. Configure AWS credentials from GitHub secrets
2. `aws ec2 start-instances --instance-ids i-064332b2f768cf778`
3. `aws ec2 wait instance-running` — blocks until instance is in running state
4. Install OpenTofu
5. `tofu init && tofu apply -auto-approve` — refreshes from AWS API, reads new public IP, updates Cloudflare DNS record

### `.github/workflows/stop-instance.yml`

1. Configure AWS credentials from GitHub secrets
2. `aws ec2 stop-instances --instance-ids i-064332b2f768cf778`

No `tofu apply` on stop — DNS is left pointing to the stale IP (harmless while the instance is off).

## GitHub Secrets Required

| Secret | Value source |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key |
| `TF_VAR_cloudflare_api_token` | Cloudflare API token |
| `TF_VAR_cloudflare_account_id` | Cloudflare account ID |

These are already set on the repository. To recreate:

```bash
gh secret set AWS_ACCESS_KEY_ID --repo club-vanta/alter-tracker-backend --body "AKIA..."
gh secret set AWS_SECRET_ACCESS_KEY --repo club-vanta/alter-tracker-backend --body "..."
gh secret set TF_VAR_cloudflare_api_token --repo club-vanta/alter-tracker-backend --body "cfut_..."
gh secret set TF_VAR_cloudflare_account_id --repo club-vanta/alter-tracker-backend --body "..."
```

## Why DNS updates correctly on start

`tofu apply` always refreshes from the live AWS API before planning. After `wait instance-running`, the instance has a new public IP assigned. Terraform reads it, diffs against the current Cloudflare DNS record, and updates it. No manual intervention needed.
