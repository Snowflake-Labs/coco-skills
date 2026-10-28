# Connect Supaflow to Cortex Code

Use a release with its registered public OAuth client ID configured. A draft
package containing `REPLACE_WITH_PUBLIC_CORTEX_CLIENT_ID` cannot authenticate.

1. Sign in to [Supaflow](https://app.supa-flow.io/sign-in), or
   [create an account](https://app.supa-flow.io/sign-up).
2. An organization administrator or personal-account owner selects the intended
   account in [Settings > API Keys](https://app.supa-flow.io/settings/api-keys),
   opens **Permissions**, enables **Enable API access**, and selects
   **Snowflake Cortex Code** under **OAuth applications**. Save changes, review,
   and save access settings. If the application is unavailable, its server-side
   registration has not been deployed; contact support. ChatGPT, Claude, CLI,
   and native-agent grants do not grant this integration access.
3. Load the plugin in Cortex Code. Use `/mcp` to connect the `supaflow` server and
   complete browser OAuth. Review the Supaflow account, organization, requested
   permissions, and callback before authorizing. Do not paste credentials into
   chat. Cortex Code owns token storage and refresh; the package contains no
   client secret or bearer token.
4. Ask: "Connect to Supaflow, show my workspaces, and let me choose one. Keep
   setup read-only." Verify `auth_status`, then `workspaces_list`, then existing
   connections in the selected workspace. A connected label alone is not proof
   of account or workspace access.

The Snowflake connection selected with `cortex -c <connection>` authenticates
Cortex Code to Snowflake. It does not authenticate Supaflow or choose a Supaflow
workspace. Never forward Snowflake credentials to Supaflow.

The callback normally uses `http://localhost:8585/callback`. Keep port 8585 free.
If CoCo selects another port, stop and resolve the conflict or use a separately
reviewed registration containing that exact callback. Do not broaden callback
registration or enable dynamic client admission as a workaround.

On denial, check the selected account, administrator role, deployed application
binding, and saved access settings before reconnecting. Do not change policy or
switch to API keys automatically. If a manual server named `supaflow` already
exists, inspect its endpoint and have the user choose which registration to
retain; user-level configuration can override plugin MCP configuration.

No Supaflow CLI, API key, local agent, Docker, or Snowflake SQL object is required
for the hosted workflow. Configure new datasource credentials securely in the
[Supaflow dashboard](https://app.supa-flow.io/dashboard). Private-agent lifecycle
operations use a separate local Supaflow CLI workflow.

## Optional: run pipelines inside Snowflake

Ask Cortex Code to use [setup-supaflow-native-app](skills/setup-supaflow-native-app/SKILL.md).
This CoCo-only skill checks the selected Snowflake account and existing apps,
then prepares installation or resumes incomplete setup. Native App SQL uses
CoCo's built-in Snowflake connection, not Supaflow MCP. Review the target,
permissions, and compute costs before authorizing changes.

Complete Network access and Sign in to Supaflow in the app's Snowsight
Configuration. Browser sign-in normally deploys the native agent automatically.
Approve that agent in Supaflow Settings > Agents, check connector readiness,
and configure the intended workspace to use it. CoCo OAuth does not replace
Native App OAuth. Installation alone does not authorize running pipelines.

If you already have a controller datasource and want it to manage the Native
App, ask the same skill to check its permissions. CoCo identifies the actual
controller role, checks existing access, presents any missing application-role
grant for approval, and verifies it without starting or stopping the agent.
This optional step does not create a controller or configure automatic wake-up.

For custom connector endpoints, ask the skill to add network access. It follows
the [documented customer-rule procedure](https://www.supa-flow.io/docs/settings/snowflake-native-app#advanced-add-custom-connector-network-access):
discover the app's bound external-access integration, review exact hosts/ports,
and attach a customer-owned rule while preserving all existing rules. Network
changes require approval; connection or workload tests are separately scoped.

This skill is bundled for CoCo CLI. The Snowsight external MCP connector alone
does not install the skill; web skill distribution requires separate validation.
