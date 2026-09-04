# Source Platform Access Verification

Verify ability to retrieve artifacts from source platform BEFORE detailed assessment.

## AWS S3

**Step 1: List available profiles**
```bash
aws configure list-profiles
```

**Step 2: Ask user which profile to use**
```
I found the following AWS profiles configured:
- default
- prod_account  
- se_sandbox_contributor

Which profile should I use for accessing s3://<bucket>/<path>?
```

**Step 3: Verify access with selected profile**
```bash
aws sts get-caller-identity --profile <selected_profile>
aws s3 ls s3://bucket-name/path/ --profile <selected_profile>
```

**Step 4: Use profile for all subsequent AWS operations**
```bash
aws s3 cp s3://bucket/model.pkl ./model.pkl --profile <selected_profile>
```

## Azure Blob Storage

**Step 1: List available subscriptions**
```bash
az account list --output table
```

**Step 2: Ask user which subscription to use**

**Step 3: Set and verify access**
```bash
az account set --subscription <selected_subscription>
az storage blob list --account-name <account> --container-name <container>
```

## GCP Cloud Storage

**Step 1: List available configurations**
```bash
gcloud config configurations list
```

**Step 2: Ask user which configuration to use**

**Step 3: Activate and verify access**
```bash
gcloud config configurations activate <selected_config>
gsutil ls gs://bucket-name/path/
```

## Databricks / MLflow

**Step 1: List available profiles**
```bash
databricks auth profiles
```

**Step 2: Ask user which profile to use**

**Step 3: Verify access**
```bash
databricks auth describe --profile <selected_profile>
```

## If No Profiles Found

Ask user:
```
No configured profiles found for <platform>. Options:
1. Configure credentials now
2. Provide credentials manually
3. Download file yourself and provide local path
4. Use a pre-signed URL
```

**⚠️ STOP if access fails:** Resolve before proceeding to assessment phase.
