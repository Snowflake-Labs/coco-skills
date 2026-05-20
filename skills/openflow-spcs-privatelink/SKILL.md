---
name: openflow-spcs-privatelink
title: OpenFlow SPCS PrivateLink
summary: Set up AWS PrivateLink between Snowflake OpenFlow on SPCS and private data sources like RDS or on-prem databases.
description: "Use when configuring private connectivity from OpenFlow (running on Snowpark Container Services) to private AWS or on-prem data sources via PrivateLink, NLB, and External Access Integration. Triggers: OpenFlow PrivateLink, SPCS private connectivity, NLB for OpenFlow, EAI for OpenFlow, SYSTEM$PROVISION_PRIVATELINK_ENDPOINT, OpenFlow RDS, OpenFlow MySQL, OpenFlow PostgreSQL, private ingestion, SPCS egress."
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
prompt: Help me set up PrivateLink between OpenFlow on SPCS and my RDS MySQL instance.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

## Overview

Connect OpenFlow (running on SPCS) to a private AWS or on-prem data source through AWS PrivateLink. This skill assumes OpenFlow is already deployed; deployment is out of scope. AWS only — Azure path differs.

Architecture:

```
OpenFlow (SPCS)
  → External Access Integration
  → Network Rule (PRIVATE_HOST_PORT)
  → Snowflake-side PrivateLink Endpoint
  → AWS VPC Endpoint Service
  → Internal NLB (TCP listeners on custom ports > 1024)
  → Target Groups (IP-based)
  → RDS / on-prem DB (via TCP proxy if non-RFC1918)
```

## Prerequisites

- AWS permissions: NLB, target groups, endpoint services, security groups
- Snowflake `ACCOUNTADMIN`
- Private data source in a VPC (or reachable via Direct Connect / VPN)
- OpenFlow runtime already deployed on SPCS

## Phase 1: AWS Infrastructure

**1. IP-based Target Groups** — one per database, registering each instance's private IP on its native port (MySQL `3306`, Postgres `5432`, etc.). For on-prem sources outside RFC1918/6598 ranges, register the IP of an EC2 TCP proxy (NGINX `stream`, HAProxy TCP mode, or Envoy) instead.

```bash
aws elbv2 create-target-group --name <db>-tg --protocol TCP \
  --port <db-port> --target-type ip --vpc-id <vpc-id>
aws elbv2 register-targets --target-group-arn <tg-arn> \
  --targets Id=<private-ip>,Port=<db-port>
```

Wait until `describe-target-health` reports `healthy` before continuing.

**2. Internal NLB** — TCP listeners on unique custom ports > 1024 (e.g. 3301, 3302). Required when multiple instances share a backend port. Enable cross-zone load balancing.

```bash
aws elbv2 create-load-balancer --name of-nlb --type network \
  --scheme internal --subnets <subnet1> <subnet2>
aws elbv2 create-listener --load-balancer-arn <nlb-arn> \
  --protocol TCP --port <listener-port> \
  --default-actions Type=forward,TargetGroupArn=<tg-arn>
```

Record the NLB DNS name.

**3. VPC Endpoint Service** fronting the NLB. Keep manual acceptance (default) for security.

```bash
aws ec2 create-vpc-endpoint-service-configuration \
  --network-load-balancer-arns <nlb-arn>
```

Record the service name (`com.amazonaws.vpce.<region>.vpce-svc-...`).

## Phase 2: Snowflake Wiring

**1. Authorize Snowflake's principal.** Get it from Snowflake, then add to AWS:

```sql
SELECT key, value FROM TABLE(FLATTEN(INPUT =>
  PARSE_JSON(SYSTEM$GET_PRIVATELINK_CONFIG())));
```

```bash
aws ec2 modify-vpc-endpoint-service-permissions \
  --service-id <svc-id> --add-allowed-principals '<principal-arn>'
```

**2. Provision and accept the endpoint:**

```sql
USE ROLE ACCOUNTADMIN;
SELECT SYSTEM$PROVISION_PRIVATELINK_ENDPOINT(
  '<endpoint_service_name>', '<nlb_dns_name>');
```

```bash
aws ec2 accept-vpc-endpoint-connections \
  --service-id <svc-id> --vpc-endpoint-ids <vpce-id>
```

Poll until `status` is `available` (30–40s):

```sql
SELECT SYSTEM$GET_PRIVATELINK_ENDPOINTS_INFO();
```

**3. Network Rule + EAI:**

```sql
CREATE OR REPLACE NETWORK RULE <rule>
  MODE = EGRESS
  TYPE = PRIVATE_HOST_PORT
  VALUE_LIST = ('<nlb_dns_name>:3301','<nlb_dns_name>:3302');

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION <eai>
  ALLOWED_NETWORK_RULES = (<rule>) ENABLED = TRUE;
```

**4. Associate the EAI with the runtime** in the OpenFlow UI: Runtime console → `...` → **Associate External Access Integration**.

**5. Test** by pointing an OpenFlow connector at `<nlb_dns_name>:<listener-port>`.

## Common Mistakes

- **Uppercase NLB DNS in `VALUE_LIST`** — `PRIVATE_HOST_PORT` is case-sensitive; use lowercase or `CREATE NETWORK RULE` fails with `Invalid VALUE_LIST`.
- **Reusing backend port as NLB listener port** — two MySQL instances on `3306` need distinct listener ports (e.g. 3301, 3302).
- **Forgetting to accept the endpoint connection** in AWS — endpoint stays `pendingAcceptance` forever.
- **Skipping target health check** — provisioning the Snowflake endpoint before targets are healthy hides RDS security group issues.
- **Wrong target type** — RDS requires `target-type ip`, not `instance`.
- **Non-RFC1918 on-prem IPs registered directly** — NLB rejects them; you need a TCP proxy EC2 in your VPC.
- **Deleting the NLB before `SYSTEM$DEPROVISION_PRIVATELINK_ENDPOINT`** — leaves a dangling endpoint. Tear down Snowflake objects first, then AWS.
- **Endpoint stuck `failed`** — deprovision, then reprovision. Use `SYSTEM$RESTORE_PRIVATELINK_ENDPOINT` within 7 days if you deprovisioned the wrong one.
