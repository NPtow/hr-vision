# HR Vision: Strands harness pilot on YC

This experiment uses the ready-made `strands-harness` package, an existing
CLIProxyAPI chat model, and HR Vision's deployed Jev transcript search adapter.
AWS credentials are not needed. This is self-hosted Strands, not the managed
AgentCore service.

The first live YC run is complete: see [acceptance results](./acceptance.md),
[raw receipts](./acceptance-20261025.json), and the [UI integration plan](./integration-plan.md).

Only synthetic fixtures are available. No production database, candidate workflow,
public route, or frontend is changed. The package supplies the agent loop, tool
selection, context management and durable conversation snapshots. Our code provides
five scoped read tools and a bounded test entrypoint.

## Configuration

Use a private environment file outside the source checkout containing
`HR_LLM_BASE_URL`, `HR_LLM_API_KEY`, `HR_AGENT_MODEL`, and the existing
`HR_JEV_API_KEY`, `HR_JEV_MODEL`, `HR_JEV_PROXY_URL` values. Do not commit it.
Set `HR_AGENT_SOURCE_DIR=/hrserver` and mount a pinned HR Vision server release there
read-only. The Jev adapter needs curl and the already configured SOCKS egress.

Mount writable persistent state at `/state`. All other paths are read-only. Run the
container without host shell/file tools, with 512 MiB memory, one request at a time,
a CPU cap, and a hard invocation timeout. All built-in filesystem, shell, browser,
subagent and environment plugins are disabled. The context manager's own scoped
artifact retrieval remains available. Cross-conversation memory is disabled for
this first pilot; conversation state is persisted per candidate and employer.

Example container arguments (credentials are passed separately):

```text
--conversation pilot-20261025
--candidate pilot_candidate_alpha
--question Как кандидат работает с возражениями?
--output /state/results/alpha-1.json
```

Use the same conversation and candidate in a new process to test restoration.
Switching candidate changes the derived session key. Save each result for review.
Successful tool calls and session restore prove mechanics, not hiring accuracy.
Review factual support, speaker attribution and source IDs separately.

## Integration after the pilot

Connect the harness through the existing backend to the current Assistant tab.
The backend supplies authorized vacancy, candidate, recording and revision scope;
Jev remains the retrieval tool. Stream text and tool status, restore conversations,
validate source IDs and reuse the existing video citation component.
Service deployment must use a tracked immutable release, a private environment
file, a persistent state directory and container resource limits.

Sources checked on 2026-10-25:
- https://strandsagents.com/docs/user-guide/harness/
- https://strandsagents.com/docs/user-guide/harness/configure/model/
- https://strandsagents.com/docs/user-guide/harness/configure/sessions/
- https://strandsagents.com/docs/user-guide/harness/configure/tools-and-instructions/
