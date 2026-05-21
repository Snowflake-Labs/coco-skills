# Troubleshooting: OpenFlow SPCS PrivateLink

## Network Rule `VALUE_LIST is not valid`

This is the most common error during Phase 2 Step 5. Check the following in order:

1. **Stray characters in the DNS name**: Ensure no extra characters like `>`, `<`, spaces, or trailing slashes exist in the value list entries. Example of a common typo:
   ```sql
   -- WRONG: stray '>' character
   VALUE_LIST = ('my-nlb.elb.us-east-1.amazonaws.com>:3306')
   -- CORRECT:
   VALUE_LIST = ('my-nlb.elb.us-east-1.amazonaws.com:3306')
   ```

2. **Private endpoint not yet `available`**: The network rule with `TYPE = PRIVATE_HOST_PORT` requires the PrivateLink endpoint to be fully provisioned. Verify:
   ```sql
   SELECT SYSTEM$GET_PRIVATELINK_ENDPOINTS_INFO();
   ```
   Status must be `available`, not `pendingAcceptance` or `provisioning`.

3. **DNS name mismatch**: The DNS name in `VALUE_LIST` must exactly match the `nlb_dns_name` passed to `SYSTEM$PROVISION_PRIVATELINK_ENDPOINT`. Compare against the `endpointHost` in the output of `SYSTEM$GET_PRIVATELINK_ENDPOINTS_INFO()`.

4. **Wrong TYPE or MODE**: Must be exactly `TYPE = PRIVATE_HOST_PORT` (not `HOST_PORT`) and `MODE = EGRESS` (not `INGRESS`).

5. **Incorrect format**: Each entry must be `'host:port'` — no protocol prefix, no quotes around the whole list:
   ```sql
   -- WRONG:
   VALUE_LIST = ('tcp://my-nlb...:3306')
   VALUE_LIST = ('10.0.1.5:3306')   -- IPs not allowed, must be DNS
   -- CORRECT:
   VALUE_LIST = ('my-nlb.elb.us-east-1.amazonaws.com:3306')
   ```

6. **Missing port**: Every entry must include a port. Use `:0` to allow any port on that host.

---

## NLB Target Group Unhealthy

1. **RDS security group missing inbound rule**: The most common cause. Add an inbound rule to the RDS instance's security group:
   - **Type**: MySQL/Aurora (or Custom TCP)
   - **Protocol**: TCP
   - **Port**: 3306 (or your database port)
   - **Source**: The NLB's security group ID, or if the NLB has no security group (internal NLBs created before 2023), use the NLB's private subnet CIDRs

2. **RDS IP address changed**: RDS private IPs can change after maintenance, failover, or reboot. Resolve the current IP and compare with the target group:
   ```bash
   nslookup <your-rds-endpoint>.rds.amazonaws.com
   ```
   If the IP changed, update the registered targets in the target group:
   ```bash
   # Deregister old IP
   aws elbv2 deregister-targets \
     --target-group-arn <target-group-arn> \
     --targets Id=<old-ip>,Port=3306

   # Register new IP
   aws elbv2 register-targets \
     --target-group-arn <target-group-arn> \
     --targets Id=<new-ip>,Port=3306
   ```

3. **Health check misconfiguration**: Go to **EC2 > Target Groups > Health checks** and verify:
   - **Protocol**: TCP
   - **Port**: 3306 (or "traffic port")

4. **VPC/AZ mismatch**: NLB subnets must be in the same VPC as the RDS instance and ideally in overlapping AZs.

---

## OpenFlow Connector Cannot Reach Data Source

Walk through these checks in order:

1. **Private endpoint available?**
   ```sql
   SELECT SYSTEM$GET_PRIVATELINK_ENDPOINTS_INFO();
   ```

2. **EAI properly associated?** Verify via the OpenFlow UI: navigate to the runtime, click **...** > **Associate External Access Integration**, and confirm your custom EAI is selected.

3. **NLB targets healthy?** Check in AWS: **EC2 > Target Groups** — targets must show healthy.
   ```bash
   aws elbv2 describe-target-health \
     --target-group-arn <target-group-arn>
   ```

4. **Connector using correct hostname and port?** The OpenFlow MySQL connector must use the **NLB DNS name** and **NLB listener port** (e.g., 3301), not the RDS endpoint or native port (3306) directly.

5. **Database credentials correct?** Verify the MySQL username/password in the connector configuration are valid for the target RDS instance.

---

## `SYSTEM$PROVISION_PRIVATELINK_ENDPOINT` Fails

1. **Snowflake account principal not authorized**: Retrieve the principal ARN:
   ```sql
   SELECT key, value FROM TABLE(FLATTEN(INPUT =>
     PARSE_JSON(SYSTEM$GET_PRIVATELINK_CONFIG()))
   );
   ```
   Add the `privatelink-account-principal` ARN to **VPC > Endpoint Services > Allow principals** in AWS.

2. **Endpoint service name incorrect**: The service name must match exactly (e.g., `com.amazonaws.vpce.us-east-1.vpce-svc-0ea9e`). Copy it directly from the AWS console.

3. **Region mismatch**: The Snowflake account and the VPC Endpoint Service must be in the same AWS region.

4. **Missing ACCOUNTADMIN role**: This function requires ACCOUNTADMIN:
   ```sql
   USE ROLE ACCOUNTADMIN;
   ```
