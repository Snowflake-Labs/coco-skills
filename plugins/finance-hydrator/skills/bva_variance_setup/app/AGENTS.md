# Snowflake App Runtime

This project deploys as an Application Service on Snowflake via `snow app deploy`.

## Key APPLICATION SERVICE Commands

| Command | Description |
|---------|-------------|
| `DESCRIBE APPLICATION SERVICE <name>` | View service details and configuration |
| `SHOW APPLICATION SERVICES` | List all deployed application services |
| `ALTER APPLICATION SERVICE <name> SUSPEND` | Pause the service |
| `ALTER APPLICATION SERVICE <name> RESUME` | Resume a suspended service |
| `ALTER APPLICATION SERVICE <name> UPGRADE` | Upgrade to a new version |
| `CALL SYSTEM$GET_APPLICATION_SERVICE_LOGS('<database>.<schema>.<app_name>')` | Retrieve service logs |
| `GRANT USAGE ON APPLICATION SERVICE <name> TO ROLE <role>` | Grant access to roles |

## Key CLI Commands

- `snow app deploy` — Deploy or update the application service
- `snow app events` — Stream deployment events and logs
- `snow app open` — Open the deployed app in your browser
- `snow app teardown --force` — Remove the deployed service

## Project Structure

This Next.js project uses `app.yml` to configure the deployment. The app runs on HTTPS and is accessible via the generated Application Service endpoint in Snowflake.

## Next Steps

For complete reference on building, developing, deploying, and operating Snowflake Apps, invoke the **snowflake-apps** skill.
