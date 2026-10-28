from typing import Any

import httpx

from app.config import settings


class SandboxExecutor:
    def __init__(self, base_url: str = settings.SANDBOX_URL) -> None:
        self.base_url = base_url

    async def execute(
        self,
        code: str,
        run_id: str,
        timeout: int = settings.SANDBOX_TIMEOUT,
        restricted: bool = False,
    ) -> dict[str, Any]:
        # The run's CURRENT dataset path (train.csv after the split). Without this
        # the sandbox defaults `dataset_path` to the ORIGINAL upload and every
        # post-split template silently reads holdout rows too (2026-07-02 bug).
        from app.core.context import get_dataset_path
        # Clamp to the sandbox's accepted range [1, 600]: it returns 400 for a
        # timeout > 600, which would fail the whole agent (the streaming candidate
        # hit this with 900). Clamp here so no caller can ever trip it.
        timeout = max(1, min(int(timeout), 600))
        payload = {"code": code, "run_id": run_id, "timeout": timeout,
                   "restricted": restricted}
        ds = get_dataset_path()
        if ds:
            payload["dataset_path"] = ds
        async with httpx.AsyncClient(timeout=timeout + 10) as client:
            response = await client.post(f"{self.base_url}/execute", json=payload)
            response.raise_for_status()
            return response.json()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


_executor: SandboxExecutor | None = None


def get_executor() -> SandboxExecutor:
    global _executor
    if _executor is None:
        _executor = SandboxExecutor()
    return _executor
