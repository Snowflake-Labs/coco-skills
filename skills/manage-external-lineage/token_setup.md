# Creating a Programmatic Access Token (PAT)

## Prerequisites
- Access to Snowsight
- User with appropriate privileges

## Steps to Create PAT

### 1. Open Snowsight
Navigate to your Snowflake account URL: `https://<ACCOUNT>.snowflakecomputing.com`

### 2. Go to User Settings
1. Click your username in the bottom-left corner
2. Select **My Profile**

### 3. Create New Token
1. Scroll to **Programmatic access tokens** section
2. Click **+ Generate new token**
3. Configure:
   - **Name:** `external-lineage-token` (or descriptive name)
   - **Expires:** Choose expiration (recommend 90 days for automation)
   - **Comment:** Optional description

### 4. Copy and Save Token
1. Click **Generate**
2. **IMPORTANT:** Copy the token immediately - it won't be shown again
3. Save to a secure file:
```bash
echo "your-token-here" > ~/.snowflake/lineage_token.txt
chmod 600 ~/.snowflake/lineage_token.txt
```

## Using the Token

### With send_lineage.sh script
```bash
./send_lineage.sh -a ACCOUNT -t ~/.snowflake/lineage_token.txt -p payload.json
```

### With curl directly
```bash
TOKEN=$(cat ~/.snowflake/lineage_token.txt)
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Snowflake-Authorization-Token-Type: PROGRAMMATIC_ACCESS_TOKEN" \
  ...
```

## Token Best Practices

1. **Never commit tokens to git** - Add token files to `.gitignore`
2. **Use restrictive permissions** - `chmod 600` on token files
3. **Rotate regularly** - Create new tokens before expiry
4. **Use descriptive names** - Makes audit easier
5. **Delete unused tokens** - Remove from Snowsight when no longer needed

## Alternative: JWT Key-Pair Authentication

For automated systems, consider key-pair authentication:

1. Generate key pair:
```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

2. Assign public key to user:
```sql
ALTER USER <username> SET RSA_PUBLIC_KEY='<public_key_content>';
```

3. Use `-j` flag with send_lineage.sh for JWT auth:
```bash
./send_lineage.sh -a ACCOUNT -t jwt_token.txt -p payload.json -j
```
