"""Runtime external-evidence research through Tavily Search + Extract.

Flow: gap detector → safe query builder (fails closed) → Tavily Search → source
gate (publisher, version, relevance, prompt-injection screen) → Tavily Extract of
the best 1-3 accepted URLs → ExternalEvidence records with stable source_refs.

Trust boundary B6: everything Tavily returns is untrusted text. It can inform a
hypothesis or supply a parameter NAME that the installed library must then
confirm; it can never change the template allow-list, budgets, the evaluation
protocol or the decision. No Tavily request id is ever invented — when the API
omits one, receipts carry a local correlation id labelled as local.
"""
from __future__ import annotations

import difflib
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

from app.autopilot import mlcore
from app.autopilot.schemas import ExternalEvidence, Finding, ProviderCall, utcnow
from app.autopilot.templates import PROBE_OBJECTS, _installed_params

OFFICIAL_HOSTS = {"scikit-learn.org": "scikit-learn (official docs)"}
MAINTAINER_PREFIXES = {"github.com/scikit-learn/scikit-learn": "scikit-learn maintainers (GitHub)"}
_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|rules)|system prompt|you are now|"
    r"disregard (the|your) (rules|instructions)|run (this|the following) (command|code)|curl\s+https?://|"
    r"rm\s+-rf|approve (this|the) (model|deployment)|set (the )?threshold to)", re.I)
_SECRET = re.compile(r"(sk-[A-Za-z0-9]{8,}|tvly-[A-Za-z0-9]{6,}|AKIA[0-9A-Z]{12,}|[A-Fa-f0-9]{32,}|"
                     r"[\w.+-]+@[\w-]+\.[\w.]+|eyJ[A-Za-z0-9_-]{10,})")
_VERSION = re.compile(r"\b(?:version\s*|v)?(\d+)\.(\d+)(?:\.\d+)?\b", re.I)


class ResearchUnavailable(Exception):
    pass


# ── 1. Gap detection ─────────────────────────────────────────────────────────
def detect_gap(findings: list[Finding]) -> dict[str, Any]:
    kinds = {f.kind: f for f in findings}
    if "pipeline_error" in kinds and kinds["pipeline_error"].value.get("keyword"):
        return {"required": True, "reason_code": "version_error",
                "finding_ids": [f.finding_id for f in findings if f.kind in ("pipeline_error", "dependency_change")]}
    if "dependency_change" in kinds:
        return {"required": True, "reason_code": "dependency_change",
                "finding_ids": [kinds["dependency_change"].finding_id]}
    return {"required": False, "reason_code": "not_needed", "finding_ids": []}


# ── 2. Safe query ────────────────────────────────────────────────────────────
def build_query(findings: list[Finding], forbidden_terms: set[str]) -> dict[str, Any]:
    """Only library identifiers enter the query. Fails closed (returns blocked)
    if anything looks like a secret or a dataset term."""
    by = {f.kind: f for f in findings}
    pe = by.get("pipeline_error")
    dep = by.get("dependency_change")
    obj = pe.value.get("object") if pe else None
    kw = pe.value.get("keyword") if pe else None
    exc = pe.value.get("exception") if pe else None
    pkg, ver = "scikit-learn", None
    if dep and "scikit-learn" in dep.value:
        ver = dep.value["scikit-learn"]["to"]
    parts = [pkg]
    if ver and re.fullmatch(r"\d+\.\d+(\.\d+)?", str(ver)):
        parts.append(".".join(str(ver).split(".")[:2]))
    if obj in PROBE_OBJECTS:
        parts.append(obj)
    if kw and re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", kw):
        parts += [f"{kw} parameter", "removed renamed"]
    if exc in ("TypeError", "ValueError", "AttributeError"):
        parts.append(f"{exc} unexpected keyword argument")
    query = " ".join(parts)
    lowered = query.lower()
    leaked = sorted(t for t in forbidden_terms if t and len(t) > 2 and re.search(rf"\b{re.escape(t.lower())}\b", lowered))
    if _SECRET.search(query) or leaked:
        return {"query": None, "blocked_reason": "privacy gate: query contained a secret-like token or dataset term",
                "leaked_terms": leaked}
    return {"query": query, "blocked_reason": None, "object": obj, "keyword": kw,
            "target_version": ver, "package": pkg}


# ── 3. Tavily client ─────────────────────────────────────────────────────────
class TavilyClient:
    def __init__(self, api_key: str, base_url: str = "https://api.tavily.com", timeout_s: float = 20.0,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ResearchUnavailable("TAVILY_API_KEY not configured")
        self.api_key, self.base_url, self.timeout_s = api_key, base_url.rstrip("/"), timeout_s
        self._transport = transport

    def _post(self, op: str, payload: dict) -> tuple[dict | None, ProviderCall]:
        corr = f"local-{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as c:
                r = c.post(f"{self.base_url}/{op}", json=payload,
                           headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                return None, ProviderCall(provider="tavily", operation=op, correlation_id=corr, ok=False,
                                          status_code=r.status_code, latency_ms=ms, error=f"HTTP {r.status_code}")
            body = r.json()
            n = len(body.get("results") or [])
            return body, ProviderCall(provider="tavily", operation=op, correlation_id=corr,
                                      request_id=body.get("request_id"), ok=True, status_code=200,
                                      latency_ms=ms, result_count=n)
        except (httpx.HTTPError, ValueError) as e:
            ms = int((time.perf_counter() - t0) * 1000)
            return None, ProviderCall(provider="tavily", operation=op, correlation_id=corr, ok=False,
                                      latency_ms=ms, error=type(e).__name__)

    def search(self, query: str, max_results: int = 6, include_domains: list[str] | None = None):
        payload = {"query": query, "max_results": max_results, "search_depth": "advanced",
                   "include_answer": False, "include_raw_content": False}
        if include_domains:
            payload["include_domains"] = include_domains
        return self._post("search", payload)

    def extract(self, urls: list[str]):
        return self._post("extract", {"urls": urls, "extract_depth": "basic"})


# ── 4. Source gate ───────────────────────────────────────────────────────────
def _classify(url: str) -> tuple[str, str]:
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = f"{host}{p.path}".lower()
    for pre, pub in MAINTAINER_PREFIXES.items():
        if path.startswith(pre):
            return "maintainer_issue", pub
    for h, pub in OFFICIAL_HOSTS.items():
        if host == h or host.endswith("." + h):
            if "whats_new" in path or "release" in path or "changelog" in path:
                return "release_notes", pub
            return "official_docs", pub
    return "third_party", host or "unknown"


def _doc_version(url: str) -> tuple[int, int] | None:
    m = re.search(r"scikit-learn\.org/(\d+)\.(\d+)/", url)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _excerpt(text: str, terms: list[str], cap: int = 1200) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")          # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)          # links -> their text
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("```", "'''").replace("\\_", "_")
    low = text.lower()
    # A version-change note about the failing parameter beats the first mention of the class.
    kw = terms[-1] if len(terms) > 1 else None
    if kw:
        m = re.search(rf"`?\b{re.escape(kw.lower())}\b`?[^.]{{0,80}}\b(renamed|deprecated|removed)\b", low)
        if m:
            start = max(0, m.start() - 300)
            return text[start:start + cap]
    hits = [low.find(t.lower()) for t in terms if t and low.find(t.lower()) >= 0]
    if not hits:
        return text[:cap]
    start = max(0, min(hits) - 300)
    return text[start:start + cap]


def gate(result: dict, idx: int, q: dict, prev_version: str | None) -> ExternalEvidence:
    url = str(result.get("url") or "")
    title = str(result.get("title") or url)[:200]
    content = str(result.get("raw_content") or result.get("content") or "")
    stype, publisher = _classify(url)
    terms = [t for t in (q.get("object"), q.get("keyword")) if t]
    low = content.lower() + " " + title.lower()
    hits = sum(1 for t in terms if t.lower() in low)
    relevance = round(hits / max(1, len(terms)), 2)
    caveats: list[str] = []
    status = "ACCEPTED"
    version_match = "unknown"
    dv = _doc_version(url)
    if dv and q.get("target_version"):
        tmaj, tmin = (int(x) for x in str(q["target_version"]).split(".")[:2])
        pmaj, pmin = (int(x) for x in (prev_version or "0.0").split(".")[:2])
        if dv < (pmaj, pmin):
            version_match, status = "mismatch", "REJECTED_OUTDATED"
            caveats.append(f"documentation for {dv[0]}.{dv[1]} predates the previously working version")
        elif dv == (tmaj, tmin):
            version_match = "exact"
        else:
            version_match = "compatible"
    elif re.search(r"\b1\.(2|3|4|5|6|7|8)\b", low):
        version_match = "compatible"
    if _INJECTION.search(content) or _INJECTION.search(title):
        status = "REJECTED_UNTRUSTED"
        caveats.append("contains instruction-like text aimed at an agent; quarantined as data")
    elif status == "ACCEPTED" and relevance == 0:
        status = "REJECTED_IRRELEVANT"
        caveats.append("does not mention the failing object or parameter")
    if stype == "third_party" and status == "ACCEPTED":
        # Forums and blogs can point the way but never justify a change.
        status = "CONTEXT_ONLY"
        caveats.append("third-party source: shown for context, not used for the planner or a fix")
    ex = _excerpt(content, terms)
    return ExternalEvidence(source_ref=f"S{idx}", url=url, title=title, publisher=publisher, source_type=stype,
                            fetched_at=utcnow(), version_match=version_match, relevance=relevance, status=status,
                            caveats=caveats, excerpt=ex if status == "ACCEPTED" else "",
                            excerpt_sha256=mlcore.sha256_bytes(ex.encode()) if ex else "")


def compat_mapping(evidence: list[ExternalEvidence], obj: str | None, old: str | None) -> dict[str, Any] | None:
    """Deterministically extract 'old → new' parameter names from ACCEPTED official
    or maintainer excerpts. The new name must exist in the INSTALLED signature."""
    if not obj or not old or obj not in PROBE_OBJECTS:
        return None
    installed = set(_installed_params(obj))
    pats = [rf"`?{re.escape(old)}`?\s*(?:was|is|has been)?\s*(?:renamed|deprecated)[^.]{{0,120}}?`?([a-z_][a-z0-9_]*)`?",
            rf"(?:in favou?r of|replaced by|renamed to|use)\s+`?([a-z_][a-z0-9_]*)`?"]
    for e in evidence:
        if e.status != "ACCEPTED" or e.source_type == "third_party":
            continue
        for pat in pats:
            for m in re.finditer(pat, e.excerpt, re.I):
                new = m.group(1)
                related = old in new or difflib.SequenceMatcher(None, old, new).ratio() >= 0.6
                if new != old and new in installed and related and old in e.excerpt:
                    return {"object": obj, "from": old, "to": new, "source_ref": e.source_ref,
                            "confirmed_by": "installed signature"}
    return None


OFFICIAL_DOMAINS = ["scikit-learn.org", "github.com"]


def run_research(findings: list[Finding], forbidden_terms: set[str], prev_version: str | None,
                 client: TavilyClient, max_extract: int = 3, max_searches: int = 2) -> dict[str, Any]:
    q = build_query(findings, forbidden_terms)
    out: dict[str, Any] = {"query": q, "calls": [], "evidence": [], "state": "SEARCHING", "mapping": None}
    if not q["query"]:
        out["state"] = "BLOCKED_PRIVACY"
        return out
    # Official sources first; widen to the open web only if they returned nothing usable.
    ev: list[ExternalEvidence] = []
    seen: set[str] = set()
    for domains in ([OFFICIAL_DOMAINS, None] if max_searches > 1 else [None])[:max_searches]:
        body, call = client.search(q["query"], include_domains=domains)
        out["calls"].append(call)
        if body is None:
            if not ev:
                out["state"] = "UNAVAILABLE"
                return out
            break
        for r in body.get("results") or []:
            if r.get("url") in seen:
                continue
            seen.add(r.get("url"))
            ev.append(gate(r, len(ev) + 1, q, prev_version))
        if any(e.status == "ACCEPTED" for e in ev):
            break
    if not ev:
        out["state"] = "NO_MATCH"
        return out
    rank = {"release_notes": 0, "official_docs": 1, "maintainer_issue": 2, "third_party": 3}
    pick = sorted([e for e in ev if e.status == "ACCEPTED"],
                  key=lambda e: (rank[e.source_type], -e.relevance))[:max_extract]
    if pick:
        out["state"] = "EXTRACTING"
        xbody, xcall = client.extract([e.url for e in pick])
        out["calls"].append(xcall)
        if xbody:
            by_url = {r.get("url"): r for r in xbody.get("results") or []}
            for e in pick:
                raw = by_url.get(e.url)
                if raw and raw.get("raw_content"):
                    regated = gate({"url": e.url, "title": e.title, "raw_content": raw["raw_content"]},
                                   int(e.source_ref[1:]), q, prev_version)
                    e.status, e.caveats = regated.status, regated.caveats
                    e.excerpt, e.excerpt_sha256 = regated.excerpt, regated.excerpt_sha256
                    e.extracted = regated.status == "ACCEPTED"
            for f in xbody.get("failed_results") or []:
                u = f.get("url") if isinstance(f, dict) else f
                for e in pick:
                    if e.url == u:
                        e.caveats.append("Tavily Extract failed for this URL; search snippet only")
    out["evidence"] = ev
    out["mapping"] = compat_mapping(ev, q.get("object"), q.get("keyword"))
    out["state"] = "AVAILABLE" if any(e.status == "ACCEPTED" for e in ev) else "NO_MATCH"
    return out
