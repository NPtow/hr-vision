#!/usr/bin/env python3
"""Bootstrap the local Dograh owner using the deployed version's DB helpers.

Run inside the API container, with its existing application environment:
  PYTHONPATH=/app python /app/deploy/mashadasha/bootstrap.py
After importing a workflow, repeat with --workflow-id ID. Dograh 1.48 requires
TTS in organization V2 settings, so an incomplete voice stack is stored as a
legacy owner config plus native per-workflow overrides. Text runtime accepts
this configuration; no fabricated TTS key or validation timestamp is written.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
import stat
import sys


def load_input(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("Bootstrap input must not be a symbolic link")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Bootstrap input must be a private regular file")
    data = json.loads(path.read_text())
    if data.get("version") != 1 or data.get("email") != "nikita@dograh.example.com":
        raise ValueError("Unexpected bootstrap file identity or version")
    password = data.get("password")
    if not isinstance(password, str) or not 16 <= len(password.encode()) <= 72:
        raise ValueError("Invalid bootstrap password length")
    providers = data.get("providers", {})
    for section in ("llm", "stt"):
        if not isinstance(providers.get(section), dict) or not providers[section].get("api_key"):
            raise ValueError("Missing LLM or STT credential")
    if providers.get("tts") is not None and not providers["tts"].get("api_key"):
        raise ValueError("TTS configuration must include a real credential")
    return data


def merge_workflow_settings(settings: dict | None, providers: dict) -> dict:
    result = copy.deepcopy(settings or {})
    if result.get("model_configuration_v2_override"):
        raise ValueError("Workflow already has a V2 override; refusing to replace it")
    overrides = dict(result.get("model_overrides") or {})
    for section in ("llm", "stt", "tts"):
        if providers.get(section) is not None:
            overrides[section] = copy.deepcopy(providers[section])
    overrides["is_realtime"] = False
    result["model_overrides"] = overrides
    return result


async def bootstrap(data: dict, workflow_ids: list[int]) -> dict:
    # Load app dependencies only inside the container. Keep the host-side
    # parsing and static checks independent of its Python package environment.
    from loguru import logger

    logger.remove()  # Avoid SQL/validation exception messages containing secrets.
    from api.constants import AUTH_PROVIDER, ENABLE_SIGNUP
    from api.db import db_client
    from api.enums import OrganizationConfigurationKey, UserConfigurationKey
    from api.schemas.auth import LoginRequest
    from api.schemas.ai_model_configuration import (
        EffectiveAIModelConfiguration,
        OrganizationAIModelConfigurationV2,
    )
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_workflow,
    )
    from api.utils.auth import hash_password, verify_password

    if AUTH_PROVIDER != "local" or ENABLE_SIGNUP:
        raise ValueError("Bootstrap requires local authentication and disabled signup")
    LoginRequest(email=data["email"], password=data["password"])
    providers = data["providers"]
    effective = EffectiveAIModelConfiguration.model_validate(
        {**providers, "is_realtime": False}
    )
    try:
        user = await db_client.get_user_by_email(data["email"])
        created = user is None
        if created:
            user = await db_client.create_user_with_email(
                email=data["email"], password_hash=hash_password(data["password"]), name="Nikita"
            )
        elif not user.password_hash or not verify_password(data["password"], user.password_hash):
            raise ValueError("Existing account password differs; refusing to reset it")

        org, _ = await db_client.get_or_create_organization_by_provider_id(
            org_provider_id=f"org_{user.provider_id}", user_id=user.id
        )
        await db_client.add_user_to_organization(user.id, org.id)
        await db_client.update_user_selected_organization(user.id, org.id)
        # Terminal sentinel means owner-managed provisioning, not voice readiness.
        # It prevents auth middleware from creating external MPS/billing/SIP accounts.
        await db_client.upsert_configuration(
            org.id, OrganizationConfigurationKey.ORGANIZATION_BOOTSTRAP.value,
            {"status": "completed", "provisioning": "owner_managed_byok"},
        )
        await db_client.upsert_configuration(
            org.id, OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value, {"value": 1}
        )
        onboarding = await db_client.get_user_configuration_value(
            user.id, UserConfigurationKey.ONBOARDING.value
        ) or {}
        await db_client.upsert_user_configuration_value(
            user.id, UserConfigurationKey.ONBOARDING.value, {**onboarding, "skipped": True}
        )
        await db_client.update_user_configuration(user.id, effective)

        if effective.tts is not None:
            configuration = OrganizationAIModelConfigurationV2.model_validate({
                "version": 2, "mode": "byok", "byok": {
                    "mode": "pipeline", "pipeline": providers
                },
            })
            # Do not call the upstream upsert helper: it stamps last_validated_at
            # despite no live provider validation having occurred in this script.
            await db_client.upsert_configuration(
                org.id, OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
                configuration.model_dump(mode="json", exclude_none=True),
                last_validated_at=None,
            )

        wanted = set(workflow_ids)
        workflows = await db_client.list_workflows_for_model_configuration_migration(org.id) if wanted else []
        selected = [workflow for workflow in workflows if workflow.id in wanted]
        if {workflow.id for workflow in selected} != wanted:
            raise ValueError("A requested workflow is missing from the owner's organization")
        workflow_updates = []
        definition_updates = []
        for workflow in selected:
            settings = merge_workflow_settings(workflow.workflow_configurations, providers)
            resolved = await get_effective_ai_model_configuration_for_workflow(
                organization_id=org.id, workflow_configurations=settings
            )
            if resolved.llm is None:
                raise ValueError("Workflow's effective LLM configuration is empty")
            workflow_updates.append((workflow.id, settings))
            for definition in workflow.definitions:
                definition_updates.append((definition.id, merge_workflow_settings(
                    definition.workflow_configurations, providers
                )))
        await db_client.bulk_update_workflow_model_configurations(
            organization_id=org.id, workflow_updates=workflow_updates,
            definition_updates=definition_updates,
        )
        return {
            "email": data["email"], "user_id": user.id, "organization_id": org.id,
            "account_created": created, "signup_enabled": False,
            "onboarding_skipped": True, "concurrent_call_limit": 1,
            "model_config_scope": "organization_v2" if effective.tts else "workflow_overrides",
            "configured_workflow_ids": sorted(wanted),
            "workflow_override_required": not bool(effective.tts) and not bool(wanted),
            "tts_configured": effective.tts is not None,
            "voice_ready": False, "runtime_verified": False,
        }
    finally:
        await db_client.engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/run/dograh-bootstrap.json"))
    parser.add_argument("--workflow-id", type=int, action="append", default=[])
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(bootstrap(load_input(args.input), args.workflow_id))))
        return 0
    except Exception as exc:
        # Pydantic and SQLAlchemy exceptions may include credential-bearing input.
        print(f"Dograh bootstrap failed ({type(exc).__name__}); no credentials printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
