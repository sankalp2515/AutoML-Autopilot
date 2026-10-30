Latest API integration and tests: [CODEX_API_HANDOFF_VALIDATION.md](CODEX_API_HANDOFF_VALIDATION.md). This supersedes the earlier account/team limitations below.

# AutoML Autopilot v5 — frontend delivery and review

The v5 repair experience was delivered in `v5/frontend` and is now integrated into
`codex/fixtures-bug-hardening` alongside backend `c61da72` for verification.
It is still branch work pending merge into `v5/main`. It replaces the primary v4 entry point with repository repair while
retaining the data incident investigator as a secondary workflow.

## Review paths

- `/repair`: home and the code-green / ML-rejected evidence story.
- `/repair/new`: sample, public Git URL or ZIP → contract → task and evaluation pack.
- `/repair/replay/churn_live_prompt_v1`: genuine rejected repairs.
- `/repair/replay/churn_live_prompt_v2`: genuine verified repair.
- `/repair/tasks/{id}`: resumable live activity, comparison, receipt and delivery.
- `/repair/runs`, `/repair/benchmark`, `/repair/how-it-works`, `/repair/docs`.
- `/repair/settings`: optional existing authentication and explicit team-service limits.

The root opens the v5 home. The production Autopilot-only flag redirects it to
`/repair`. Existing `/autopilot` routes still serve the data investigator.

## FE-01–20 coverage

“UI implemented” means working controls and a typed API integration, with mocked
browser tests for operations that would spend money or change an external service.
It does not mean those external operations were performed during this task.

| Card  | Delivered                                                                                                                                                                 | Remaining dependency / limit                                                                                                                                                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FE-01 | Brand, primary navigation, status/runner disclosure, mobile menu, theme switch                                                                                            | None for UI                                                                                                                                                                                  |
| FE-02 | Three entry paths, genuine evidence board, measured recording counts, five stages and trust strip                                                                         | EV-03/04 must supply a comparable generic-agent false-accept headline; no invented replacement                                                                                               |
| FE-03 | Genuine replay, scrub, speed, guided stops, recorded labels, no premature final evidence                                                                                  | EV-02 still needs a genuine leakage-rejection recording; the two churn outcomes remain separate runs                                                                                         |
| FE-04 | Sample gallery, HTTPS repository/ref admission, ZIP upload/drop, limits and privacy                                                                                       | Uses BE repository-staging API                                                                                                                                                               |
| FE-05 | Detected YAML editing, server validation, guided builder with live YAML and inline errors                                                                                 | Builder starts from disclosed example values; review commands/paths for each repository                                                                                                      |
| FE-06 | Test discovery/picker, objective, evaluation pack/columns/segment, full/generic mode, invite, Start                                                                       | Backend CreateTask has no per-task candidate/token ceilings or estimate endpoint. Fields disclose server policy instead of pretending to persist limits                                      |
| FE-07 | Stage line, resumable/deduplicated events, hypotheses, tool excerpts/denials, candidates/diff/terminal, budget panel, cancel/reconnect                                    | Current worker publishes some steps, source diffs and usage only at completion. UI waits for published evidence; true incremental source/usage events need backend work                      |
| FE-08 | Side-by-side independent checks, eligibility and clear green-but-rejected reasons                                                                                         | None for published report data                                                                                                                                                               |
| FE-09 | Verdict, highlighted split/unified diff, added-test paths, both receipts, metrics/delta CI, provenance, costs, reproduce guidance, downloads, consented sharing and print | CI is the recorded paired metric-change interval, not an invented interval around the absolute metric. Exact full ML reproduction needs authorized data/protocol                             |
| FE-10 | PR preview, transient token form, draft delivery integration and returned GitHub link                                                                                     | External GitHub delivery tested with a mocked endpoint; no real PR created                                                                                                                   |
| FE-11 | Scoped live history, bundled and server-published replays, search/decision/type filters, load more                                                                        | Team aggregation depends on FE-20                                                                                                                                                            |
| FE-12 | Loading/empty/error, task status, cancellation, reconnect, live-off/provider-unavailable and replay recovery                                                              | Budget/sandbox admission errors display server reasons; unavailable runtime data is never synthesized                                                                                        |
| FE-13 | Arms table, recording chart/matrix, costs, failures, limitations and secondary v4 evaluation                                                                              | EV-03/04 comparison results are missing; cells explicitly say Not measured                                                                                                                   |
| FE-14 | Five stages, target trust zones, permitted/forbidden agent actions, actual status/readiness and VERIFIED limits                                                           | Target architecture is labelled separately from runtime evidence                                                                                                                             |
| FE-15 | Getting started, YAML example/reference, support/limits, pack format, handoff, privacy and FAQ                                                                            | Retention/access guarantees must be confirmed by the deployed service configuration                                                                                                          |
| FE-16 | Data incident link in source flow and secondary navigation                                                                                                                | Reuses existing v4 screens                                                                                                                                                                   |
| FE-17 | Reusable accessible diff, new/deleted files, syntax/within-line highlights, split/unified and copy                                                                        | Text diff viewer; no binary preview                                                                                                                                                          |
| FE-18 | Light/dark, 1280 desktop / 375 mobile, focus, keyboard tabs/dialogs, screen-reader labels, reduced motion and bounded code/table scrolling                                | Automated WCAG checks and browser AX inspection are not a substitute for a full assistive-technology user study                                                                              |
| FE-19 | v5 Playwright contracts, genuine replays, axe, legacy regression suite and opt-in actual-stack smoke                                                                      | No inference in tests; paid model execution is outside this frontend validation                                                                                                              |
| FE-20 | GitHub callback, workspace headers/creation/switching, role-aware members and CI links | Connected to BE-23/24; seeded real-API checks. Actual external OAuth/App installation remains untested. |

## Design rationale

The full token/component specification is in [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md).

1. **Evidence before exposition.** The home visual shows one real patch producing
   two different checker outcomes. This explains the product's distinguishing
   behavior without a decorative robot or a fabricated benchmark.
2. **Continuity with the Orchestrator.** A persistent navigation rail and plainly
   named sections preserve familiar orientation. The repair flow introduces one
   decision at a time: source, authority, then success conditions.
3. **Cool paper and restrained teal.** Light surfaces support long code/evidence
   sessions; navy anchors technical detail. Teal is for action/navigation. Green,
   amber and red carry verification meaning with labels and icons. Teal was
   darkened after automated contrast testing.
4. **Three fonts, three jobs.** Space Grotesk provides a recognizable heading/brand
   voice; IBM Plex Sans makes dense forms readable; IBM Plex Mono preserves code
   alignment. Existing font setup is reused.
5. **Progressive disclosure.** Plain-language decisions come first. Diffs, checker
   evidence, reasoning excerpts and raw event records remain inspectable without
   overwhelming a first-time reader.
6. **Control stays visible.** Cancellation, sharing consent, explicit draft delivery
   and token clearing are part of the interaction, not hidden assurances. Historical
   runner/provider disclosures remain beside the evidence.
7. **Responsive by task.** Desktop compares candidates; mobile stacks them and keeps
   overflow inside focusable code/table regions. Navigation becomes a native modal.
8. **Motion explains progress.** Short state transitions and a user-controlled replay
   communicate causality. Reduced-motion preferences disable decorative movement.

## Verification and integration

Validated 2026-10-30:

- Production Next build and TypeScript check passed.
- 23 browser contract/regression cases passed: 14 v5 cases and 9 legacy cases.
- 38 v5 view/state audits passed the selected WCAG A/AA axe checks, across light/dark
  themes, 1280 px desktop and 375 px mobile; the suite also checks page overflow.
- Two actual-stack smoke paths passed: original intake/replay and all-six-samples plus regional-contract review. The latter saves a real-API gallery screenshot.
- The final source/contract/discovery/pack/task test was rerun after the last form
  polish and passed. No paid inference or real GitHub write was performed.

Screenshots: [light home](assets/repair/home-light.png),
[dark home](assets/repair/home-dark.png), [1440 px](assets/repair/home-1440.png),
[mobile](assets/repair/home-mobile.png), [source](assets/repair/source-light.png),
[candidate evidence](assets/repair/candidate-evidence.png),
[verified receipt](assets/repair/verified-receipt.png).

Validation commands (from `frontend`, with a production build/server running):

```powershell
node node_modules/typescript/bin/tsc --noEmit -p .
npm run build
$env:E2E_WEB_URL='http://localhost:3111'
$env:E2E_MOCK_API='1'
npx playwright test e2e/repair.spec.ts e2e/autopilot.spec.ts --workers=1
$env:E2E_MOCK_API='0'
$env:E2E_REPAIR_LIVE='1'
npx playwright test e2e/repair-live.spec.ts --workers=1
```

The actual-stack smoke uses the real v5 router and a separate SQLite database. It
stages a bundled repository and validates its contract, then opens a genuine replay.
It stops before Start, has live execution disabled, and runs no model or GitHub call.
The local QA bootstrap disables dotenv loading and uses no provider credentials.
The production build used already-cached, real font files through Next's test
font-fetch hook; it did not require a Google Fonts network request. Product font
configuration is unchanged.

**Backend integration:** the original frontend delivery started at `8c58aeb` and
needed additional backend endpoints. The present `codex/fixtures-bug-hardening`
integration starts at `c61da72` and revalidates intake against the backend now
committed in `v5/main`; the two actual-API checks pass. This does not establish
paid inference, external PR writes or production-service acceptance.

## Visual review ledger

| Comparison                                        | What was checked / corrected                                                                                          |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Original Orchestrator interaction → v5 navigation | Persistent rail and clear section naming retained; repair becomes primary; data incident mode stays reachable         |
| Home specification → browser at desktop width     | Two-column promise/evidence composition, all three entries, real measured rejection and clear reading order           |
| Light → dark                                      | Semantic meaning retained; navy surfaces and lighter text replace simple inversion; automated contrast checked        |
| Desktop → 375 px                                  | Sidebar becomes menu; panels stack; document width stays bounded; tables and code can be keyboard-scrolled            |
| Successful → rejected recording                   | Both preserve source/check outcomes; a green code receipt cannot visually override a failed ML screen                 |
| Initial replay frame → final receipt              | Candidate diffs/checks and usage stay hidden until their recorded stage; final receipt is an explicit end-of-run view |
| Short desktop viewport                            | Navigation can scroll, keeping bottom settings/theme controls reachable                                               |

Review screenshots are generated by the opt-in screenshot case in
`repair-live.spec.ts` using the actual local API (no mocked successful provider
status). They live in `docs/assets/repair/`.

## Evidence provenance

`frontend/data/repair/v1.json` and `v2.json` are derived from the genuine
`docs/benchmarks/repair/churn_live_prompt_v1.json` and `v2.json`. Host paths are
redacted; original file SHA-256 values and source references are retained. The
historical `nvidia_api` provider and `process-unisolated` runner are disclosed.
The verified recording preserved ROC AUC at 0.9114; it did not demonstrate an
accuracy gain. These are two recordings of one fault family, not a broad benchmark.

No production-readiness or first-place guarantee is implied by visual polish.
The missing server/evaluation dependencies above remain real launch and judging
risks, and are deliberately visible instead of hidden behind sample UI.

Latest fixture/bug and remaining frontend audit: [CODEX_V5_COMPLETION_AUDIT.md](CODEX_V5_COMPLETION_AUDIT.md).

Integration revalidation also corrected the ignored Runs route and adds a visible unsupported-file rejection regression. See [six sample repositories](assets/repair/source-six-repositories.png).
