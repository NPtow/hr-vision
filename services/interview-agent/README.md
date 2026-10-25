# HR Vision interview agent

Ready-made Strands Harness hosted as a private YC service. The existing employer
assistant route authorizes the source and sends a frozen snapshot of its vacancy,
profile, assessment and transcript. The harness selects read tools and uses Jev
to search interview passages. It has no application actions or general file,
shell, web or subagent tools. A separate structured check validates grounding.

The existing response shape (`parts`, `evidence`) and video citation component
are retained. `action=history` restores completed turns after reopening a candidate.
When grounding rejects a draft, the harness gets specific issues and can repair
it once within the same deadline. A second rejection stays an error. Rejected
drafts and check results are stored only under private state for diagnosis.
Scopes include employer account, workspace, vacancy/source, content revision and
prompt version. The browser cannot supply the transcript or choose a harness session.

The main API keeps authorization, request idempotency and daily budget accounting.
The private worker caches completed requests and promotes a copied Strands session
snapshot only after successful validation. Failed turns do not replace the prior
conversation. One model run executes at a time within the 512 MiB container.
The main API rechecks access and source revisions before delivering or restoring data.
Source snapshots already supplied to an in-flight worker remain bounded to that run;
the worker cannot fetch additional production data.

## Installation

Build from a committed release with the repository root as Docker context:

```sh
docker build -f services/interview-agent/Dockerfile -t hr-vision-agent:<commit> .
```

Private `/etc/hr-vision/agent.env`, mode 0600, contains `HR_LLM_BASE_URL`,
`HR_LLM_API_KEY`, `HR_AGENT_MODEL`, `HR_JEV_API_KEY`, existing `HR_JEV_PROXY_URL`,
and a random `HR_AGENT_INTERNAL_TOKEN`. Optional `HR_JEV_MODEL` retains its current
setting. The worker binds only `127.0.0.1:8424`. No Caddy or public port is added.
`/etc/hr-vision/agent-image.env` supplies the immutable `HR_AGENT_IMAGE` tag.
State is `/var/lib/hr-vision-agent`, owner 10001:10001, directory mode 0700.

Main API configuration uses the same internal token, `HR_INTERVIEW_AGENT_URL`,
and `HR_AGENT_MODEL`. An empty URL retains the legacy executor. Provider failures
never silently fall back to it. `HR_ASSISTANT_DAILY_BUDGET` can configure the existing
per-account budget; actual returned token usage settles the initial reservation.
Raw prompts and provider exceptions are not written to logs. Private session state
contains conversation data and is not included in web artifacts or Git.

Deploy the API/UI through the existing guarded main-branch workflow. Do not bypass
the live-meeting deployment guard. The current controls and layout are unchanged;
responses still arrive as completed messages, rather than partially validated text.

## Verification

```sh
# Existing API tests, no model calls
cd interface-lab/server && python3.12 -m unittest test_interview_assistant -q
# In the worker image, mount this directory read-only as /tests
python -m unittest discover -s /tests -p test_runtime.py -v
```

Live acceptance must use the authenticated public API on the six private DSA
intakes. Check actual tool metadata, references and follow-ups, then reload the
existing UI and open a cited video moment. Synthetic unit tests do not establish
answer quality. Keep real receipts private and publish only an aggregate report.

Version: `2026-10-25.agent1`. Dependencies are pinned to the same versions as the
successful YC pilot. Source: https://strandsagents.com/docs/user-guide/harness/.
