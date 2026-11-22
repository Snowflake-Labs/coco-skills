# Sandboxed shell

Enable this plugin to run the agent's shell commands in an NVIDIA OpenShell sandbox instead
of on your machine. Use `/plugin enable sandboxed-shell`, then `/plugin reload`.

It works with CoCo CLI on macOS and Linux (on Windows, use WSL). It needs `python3` and the
`openshell` CLI with a reachable gateway. OpenShell is open source (Apache-2.0) and runs on
your machine or your own hosts, so project files stay with you. If no sandbox can be
reached, shell commands fail with exit code 125 and do not run on your machine. Read, edit,
SQL and web tools are not affected. CoCo CLI also has its own options, `/sandbox` and
`cortex --cloud`; running this plugin together with `/sandbox` is not tested.

Check your setup from your own terminal with `python3 <plugin directory>/bin/sbx.py doctor`.
