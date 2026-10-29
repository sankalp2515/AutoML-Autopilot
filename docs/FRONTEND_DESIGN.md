# AutoML Autopilot v5 — design system and implementation inventory

## Direction: evidence, made legible

The original Orchestrator is the interaction reference: a persistent navigation
rail, clearly named sections and a guided sequence. V5 changes the primary task
from training a model to repairing a repository. Preserve that clarity while
giving the repair itself the visual emphasis. The signature composition is a
two-part evidence panel: software checks alongside ML checks. Green software
tests can coexist with a rejected repair; that distinction is the product story.

The design is an engineering application with a useful home screen, not a
marketing page wrapped around a dashboard. No invented benchmark, customer,
production-readiness or isolation claims. Existing genuine recordings are a
first-class, zero-inference-cost entry path and retain their historical runner
and provider labels, including `process-unisolated`.

## Design tokens and rationale

| Decision        | System                                                                                                              | Why                                                                                                                         |
| --------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Palette         | Canvas #f4f7fa, white surfaces, ink #183042, teal #066e77, pale cyan #e0f3f4, muted slate #586c7b                   | Cool, quiet surfaces distinguish this from the legacy warm palette; teal connects the existing identity to the new workflow |
| Semantic colors | Green #17694d, amber #8a5b0c, red #a13e44; paired text/icons                                                        | Verification, uncertainty and rejection must remain distinguishable without color alone                                     |
| Dark theme      | Navy canvas #111c26, surface #182733, text #e5edf3, teal #72d4da                                                    | Preserve contrast and information hierarchy; avoid decorative neon                                                          |
| Typography      | Existing Space Grotesk for brand/headings; IBM Plex Sans for prose/controls; IBM Plex Mono only for code            | Reuse licensed font setup, give headings personality, keep technical data readable                                          |
| Type scale      | 12, 13, 14, 16, 20, 24, 32, 48–64 px                                                                                | Dense workspace text and expressive home typography share one clear hierarchy                                               |
| Spacing         | 4 px base; 8/12/16/24/32/48/64 px intervals                                                                         | Consistent alignment reduces cognitive effort across forms, traces and receipts                                             |
| Grid            | 232 px navigation; flexible main; 1450 px maximum canvas; 12-column desktop                                         | The workspace earns its density; docs and forms use narrower reading measures                                               |
| Components      | Open section layouts, quiet bordered panels, tables, stepper, evidence checks, diff, terminal, notice, dialog, tabs | Each surface corresponds to a task; no decorative stack of identical cards                                                  |
| Geometry        | 8 px controls, 12 px panels, 18 px feature/evidence stage                                                           | Hierarchical radii convey interaction scale without excessive rounding                                                      |
| Iconography     | Consistent 20/24 px outline SVGs, 1.7 px strokes, labels on actions                                                 | The same visual vocabulary supports wayfinding and accessibility                                                            |
| Illustration    | Code-native train/serve parity and verification pathways                                                            | The memorable visual explains this particular product; no stock robot imagery                                               |
| Motion          | 140–220 ms state changes; replay follows a user-controlled clock; reduced motion respected                          | Motion explains causality and feedback, rather than decorating every scroll                                                 |

## Information architecture and exact primary copy

Navigation: Home, New repair, Runs, Benchmark, How it works, Docs.
Brand: AutoML Autopilot.
Tagline: The ML coding agent that doesn't stop at green tests.
Home heading: Green tests. Proven repairs.
Primary entries: Watch a verified repair; Try a sample repo; Fix your repo.
The data-incident investigator remains a secondary path from New repair.

Home -> Source -> Contract -> Task -> Workspace -> Candidates -> Verdict -> PR.
Home foregrounds the real distinction, then explains five steps and links to
the source evidence. The workspace keeps stage, activity, hypotheses and resource
usage nearby. Verdicts start with plain language, then let readers inspect proof.

Desktop uses a stable navigation rail and a two-column evidence composition.
At 1280 px the workspace remains actionable; narrow screens stack panels,
provide a menu drawer and allow bounded scrolling only within code/tables.
Both themes include visible focus, native controls, proper labels, keyboard
dialogs, reduced motion, contrast checks and print styles.

## Truth and integration rules

The two bundled runs are genuine historical NVIDIA-path recordings, not a
matched-budget benchmark or a demonstration of hardened execution. No invented
leakage recording is added. Copy ties reported counts to their recording.
If a backend capability is missing, explain the unavailable action and offer a
recorded run. A mock API is restricted to browser tests. It is never a product
fallback pretending to have executed a repair.

All backend access lives in a typed repair client. Tokens supplied for PR delivery
stay in transient component memory, are never placed in a URL/local storage,
and are cleared after submission. Replay delivery actions do not spend or create
external PRs. Share links publish only after explicit consent.

## Review inventory

Review home, source/contract/task forms, replay, live workspace, comparison,
receipt/PR handoff, runs, benchmark, trust/docs and account/workspace states.
Verify at desktop 1440 and 1280 px and mobile 375 px; test light/dark, keyboard,
loading/error/empty/reconnecting/cancel and reduced motion. Record final evidence,
five concrete visual comparisons and FE-01..20 coverage in FRONTEND_COMPLETION.md.
