"""How the agent reaches the oracles: in-process (development) or the verifier service.

Either way the agent only sends references and diffs and receives receipts; it never
handles labels or sealed tests, and receipts are never shown to the model.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.repair import contracts as c
from app.repair.oracle.code import Baseline
from app.repair.oracle.grading import Grader, GradeRef


class LocalOracles:
    def __init__(self, runner, ref: GradeRef):
        self.g, self.ref = Grader(runner), ref

    def baseline(self) -> Baseline:
        return self.g.baseline(self.ref)

    def code(self, cid: str, diff: str) -> c.CodeReceipt:
        return self.g.code(self.ref, cid, diff)

    def ml(self, cid: str, diff: str, *, final: bool) -> c.MLReceipt:
        return self.g.ml(self.ref, cid, diff, final=final)


class RemoteOracles:
    def __init__(self, ref: GradeRef, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self.ref, self.base_url, self.token, self._transport = ref, base_url.rstrip("/"), token, transport

    def _post(self, path: str, **extra) -> dict:
        body = {"task_id": self.ref.task_id, "ecc": self.ref.ecc, "snapshot_dir": self.ref.snapshot_dir,
                "sealed_ref": self.ref.sealed_ref, **extra}
        with httpx.Client(timeout=1900, transport=self._transport) as cl:
            r = cl.post(f"{self.base_url}/v5/oracle{path}", json=body, headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code != 200:
            raise RuntimeError(f"verifier {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def baseline(self) -> Baseline:
        d = self._post("/baseline")
        return Baseline(d["outcomes"], d["parsed"])

    def code(self, cid: str, diff: str) -> c.CodeReceipt:
        return c.CodeReceipt.model_validate(self._post("/code", candidate_id=cid, diff=diff))

    def ml(self, cid: str, diff: str, *, final: bool) -> c.MLReceipt:
        return c.MLReceipt.model_validate(self._post("/ml", candidate_id=cid, diff=diff, final=final))


def make_oracles(runner, ref: GradeRef):
    if settings.REPAIR_VERIFIER_URL:
        return RemoteOracles(ref, settings.REPAIR_VERIFIER_URL, settings.REPAIR_VERIFIER_TOKEN)
    return LocalOracles(runner, ref)
