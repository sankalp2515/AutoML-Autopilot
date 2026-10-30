"""Choose the runner for this process from settings.

    REPAIR_SANDBOX_URL set  -> RemoteRunner (worker / verifier in production)
    REPAIR_RUNNER=docker    -> LocalContainerRunner (sandbox-runner service, local dev with Docker)
    REPAIR_RUNNER=process   -> ProcessRunner, unisolated: tests only
"""
from __future__ import annotations

from app.config import settings
import os
from app.repair.sandbox.base import SandboxError


def local_runner():
    if settings.REPAIR_RUNNER == "nebius":
        from app.repair.sandbox.tokenfactory import TokenFactoryRunner
        return TokenFactoryRunner(settings.REPAIR_ENV_ROOT or "/tmp/autopilot-nebius-envs",
            api_key=settings.NEBIUS_API_KEY, project_id=settings.NEBIUS_PROJECT_ID,
            base_url=settings.NEBIUS_SANDBOX_URL, layer_bytes=settings.NEBIUS_SANDBOX_LAYER_BYTES)
    if settings.REPAIR_RUNNER == "process":
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            raise SandboxError("The unisolated process runner is restricted to tests; configure Docker or the sandbox service")
        from app.repair.sandbox.process import ProcessRunner
        return ProcessRunner(settings.REPAIR_ENV_ROOT or None, allow_unisolated=True)
    from app.repair.sandbox.local import LocalContainerRunner
    return LocalContainerRunner(settings.REPAIR_ENV_ROOT or None)


def make_runner():
    if settings.REPAIR_SANDBOX_URL:
        from app.repair.sandbox.remote import RemoteRunner
        return RemoteRunner(settings.REPAIR_SANDBOX_URL, settings.REPAIR_SANDBOX_TOKEN)
    return local_runner()
