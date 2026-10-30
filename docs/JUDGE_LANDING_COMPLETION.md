# Judge access, landing and repository completion — 2026-10-30

Scope: owner balance $29.50, no duplicate promo redemption; judge-only inference protection; complete landing; DOC-01, DOC-02, DOC-06, DOC-07 and DOC-05 license portion. No Nebius API inference, sandbox integration, cloud provisioning, main merge or push was performed.

## Delivered

- A complete public landing at `/repair` (also the default product entry), with public navigation, a genuine recorded verification instrument, problem story, inspectable fixture evidence, five-stage workflow, reviewer benefits, $0 exploration/judge access terms, rate-policy explanation, objections, native FAQs and consistent replay/live calls to action. Application screens retain their workspace navigation.
- No invented customer logos/testimonials, commercial subscriptions, conversion lift or agent benchmark. Fixture stories distinguish authored checker proofs from genuine autonomous recordings. Nebius validation remains pending in the copy.
- A persistent `--judge-only` operator preset. Signed-in users still need the invite. Atomic admission enforces cumulative/daily estimated commitments, cooldown and shared active slots. Idempotent retries don't reserve another task; 429 includes Retry-After. The invite stays only in page memory during a failed request, clears on success and is not saved in browser storage.
- Judge mode persists in private operator state, so restarting without the flag cannot silently reopen public live access. Live judge mode requires Token Factory credentials and an invitation configuration and clears NVIDIA fallback; the legacy worker receives no provider keys. The default local preview remains keyless and inference-disabled.
- A v5-first README; coloured, rendered as-built SVG; legacy keep/document decision with historical README; contribution/support/security guidance and issue/PR templates; visible GPL-3.0 links with third-party notices preserved. Significant-update statement, genuine Nebius feedback, video and main merge remain separate owner/submission work.

## Local acceptance

348 fast backend tests passed with SQLite/Postgres enabled; one skipped. Four additional isolated operator-profile tests passed. The focused 34-test hardening run overlaps the fast suite. An initial 26-case browser run and six-case final targeted run cover 27 distinct mock browser scenarios, including judge recovery. Two real-stack browser cases passed; one optional screenshot case skipped. Both themes/desktop/mobile axe checks passed. Production frontend and container builds passed; eight-service readiness passed with non-superuser app DB roles. Runtime API values and legacy-worker credential absence were checked without printing secrets. Current preview: http://localhost:3135/repair.

Reviewed desktop hero, mobile dark viewport and full-page screenshots under `docs/assets/landing/`. Architecture was rendered to `docs/benchmarks/repair/judge-landing-audit/architecture-preview.png` and visually inspected. The README contract parses; 34 local documentation links resolve; issue forms parse as YAML; SVG parses. No full assistive-technology user study or measured conversion experiment is claimed.

Initial build attempts caught an unescaped JSX apostrophe; it was corrected before successful builds. Screenshots and final targeted tests use the corrected UI. Runtime checks describe a local Docker stack, not a public cloud or Nebius deployment.

## Cost and design explanations

[Judge deployment and costs](JUDGE_DEPLOYMENT_AND_COSTS.md) gives exact unit-rate arithmetic and separates inference, free beta sandbox execution and paid hosting. Google Cloud hosting plus actual Token Factory inference meets the published platform condition; the proposed sandbox integration also follows the Coding track workflow. This is not an organizer eligibility guarantee. A conservative 55-day Iowa e2-standard-4/50-GiB-balanced-disk/IPv4 fixed subtotal is $192.55 before usage/taxes. Valid unused Google trial credit may cover it; the owner's actual balance/expiry remains unknown. The $20 lifetime admission envelope leaves $9.50 of the stated inference balance unallocated but is not a provider billing lock.

[Landing design rationale](LANDING_DESIGN_RATIONALE.md) explains the purpose and conversion hypothesis of every major section, typography/color/layout choices, evidence strategy and accessibility/motion rules. No prize probability is inferred from the redesign.

Evidence and checksums: `docs/benchmarks/repair/judge-landing-audit/acceptance.json`. Frozen contracts and sandbox base interfaces were not modified. The backlog records completion and remaining external gates explicitly.
