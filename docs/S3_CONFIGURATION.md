# S3 Configuration for Daily CI

The daily CI workflow uploads test results and boot logs to an S3-compatible storage service. This provides long-term storage and easy access to test artifacts.

## Required GitHub Secrets

To enable S3 uploads, you need to configure the following secrets in your GitHub repository:

### AWS_ACCESS_KEY_ID
Your AWS access key ID or S3-compatible service access key.

### AWS_SECRET_ACCESS_KEY
Your AWS secret access key or S3-compatible service secret key.

### AWS_REGION
The AWS region where your S3 bucket is located (e.g., `us-east-1`, `eu-west-1`).

### S3_BUCKET
The name of your S3 bucket where test results will be uploaded.

## S3 Bucket Structure

Test results are organized in the S3 bucket with the following structure:

```
s3://{bucket}/daily/{date}/{run_id}/{device}-{version}/
├── report.xml          # JUnit XML test report
└── console_*.log       # Boot and console logs from labgrid
```

Where:
- `{date}`: Date in YYYY-MM-DD format (UTC)
- `{run_id}`: GitHub Actions workflow run ID
- `{device}`: Device name (e.g., `glinet_gl-mt6000`) or `qemu_{target}` for QEMU tests
- `{version}`: OpenWrt version (e.g., `snapshot`, `23.05`, `24.10`)

## Setting Up Secrets

1. Go to your repository settings: `Settings` → `Secrets and variables` → `Actions`
2. Click `New repository secret`
3. Add each of the required secrets listed above

## S3 Bucket Permissions

The AWS credentials need the following S3 permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::{bucket}/daily/*"
    }
  ]
}
```

## Using S3-Compatible Services

This configuration works with any S3-compatible service (MinIO, DigitalOcean Spaces, etc.). Simply:

1. Configure the endpoint URL by modifying the workflow to add the `--endpoint-url` parameter to AWS CLI commands
2. Use the access key and secret key from your S3-compatible service
3. Set the appropriate region for your service

## Example: Accessing Uploaded Results

After a workflow run completes, you can access the results using:

```bash
# List results for a specific date
aws s3 ls s3://{bucket}/daily/2025-01-15/

# Download all results for a specific run
aws s3 sync s3://{bucket}/daily/2025-01-15/{run_id}/ ./results/

# Download just the XML reports
aws s3 cp s3://{bucket}/daily/2025-01-15/{run_id}/ ./results/ --recursive --exclude "*" --include "*/report.xml"
```

## Troubleshooting

### Upload fails with "Access Denied"
- Verify your AWS credentials are correct
- Check that the IAM user/role has the required S3 permissions
- Ensure the bucket name is correct

### Upload fails with "Bucket does not exist"
- Verify the bucket exists in the specified region
- Check that the bucket name matches the `S3_BUCKET` secret

### Uploads succeed but files are not visible
- Check bucket permissions and ownership
- Verify the bucket's region matches `AWS_REGION`
- Ensure you're looking in the correct date/run_id path
