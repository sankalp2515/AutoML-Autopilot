# Landing page design and conversion rationale

Audience: ML engineers, technical reviewers and hackathon judges. Primary conversion: open a genuine verified recording, then inspect a diff/receipt. Secondary conversion: start an invited live repair. No signup capture, fabricated customer proof, invented price plan or assumed conversion lift.

## Direction and tokens

Preserve the product's instrument-like identity: slate ink `#183042`, paper-blue `#f4f7fa`, white surface `#ffffff`, teal `#066e77`, measured pass `#17694d`, measured rejection `#a13e44`. Dark mode uses the existing shared tokens with readable lighter counterparts. Teal denotes action, red denotes a measured rejection rather than decoration. Space Grotesk carries headings, IBM Plex Sans carries explanations and IBM Plex Mono is reserved for code. The headline is deliberately direct about ML code and tests.

Layout: centered 1,240px content on a wider header/footer, 48px desktop gutters, 20px mobile gutters; 24px component gaps, 58–90px section rhythm. The hero uses two unequal columns: promise/action left, actual evidence right. Narrative sections use alternating editorial splits, a real sequential workflow and restrained proof panels. Home has public horizontal navigation; inner application routes retain the workspace shell. This distinguishes first-contact orientation from execution without creating a second product.

Plan critique before refinement: a generic grid of promises and sponsor logos would obscure what is different here. The memorable element is instead an interactive verification instrument from a real historical receipt. Changing between software and ML checks demonstrates why a plausible patch can be rejected. It never masquerades as a new live execution. All other visual treatment stays quiet. The existing short homepage had several entry points but no access/pricing terms, objections or complete decision flow; this page fills those gaps.

## Section purpose

| Section | User decision it supports | Design/content rationale |
|---|---|---|
| Hero | What is this, who is it for, and why inspect it? | Specific ML repair promise, one dominant replay CTA and a secondary sample path; no form or paid commitment |
| Verification instrument | What does independent checking add? | Real recorded diff and rejection; user-driven switch between software and ML evidence; explicit historical isolation limit |
| Workflow context | Does it fit my engineering environment? | Python, Git diffs and independent verification rather than borrowed customer logos; actual NVIDIA evidence/provider status disclosed |
| Problem story | Why isn't a green test enough? | Serving drift, leakage and group regression explain the gap with concrete mechanisms |
| Evidence ledger | Can I check the claims? | Six repositories and source-linked authored proofs; separates checker evidence, genuine recordings and incomplete comparison |
| Fixture stories | Does it address failures like mine? | Churn, refund and credit examples with provenance and direct evidence links; not labelled customer case studies |
| Five-stage workflow | How much work and control are involved? | An actual sequence from contract to human review, with a concrete artifact at each step |
| Reviewer benefits | What do I get beyond generated text? | Diff, test, reason, constrained attempt and explicit approval; features are tied to review decisions |
| Access/pricing | What will it cost and can I try it? | $0 recorded exploration and $0 invited judge access; no fictional commercial tiers, runtime policy loaded from API |
| Rate-limit explanation | Will a limit block evaluation? | Explains finite credit, wait/slots, reopen behavior and private owner-assisted evaluation; replays remain readable |
| FAQ | What about unsupported repos, failures, privacy and trust? | Native details/summary for low-friction keyboard access; honest boundaries beside the offer |
| Final CTA/footer | What do I do next? | Repeat the same low-commitment action after evidence; source/license and docs remain discoverable |

## Interaction, accessibility and proof

Use the established line icon library. No stock imagery, animated background, auto-playing video, customer endorsements or fake scarcity. State transitions are short and user-driven; reduced-motion preferences disable transitions. Semantic headings/landmarks, visible focus, aria-pressed gate buttons, an aria-live result and native FAQ disclosure support keyboard and assistive technology. CSS adapts to desktop/mobile without hiding evidence. Browser/axe checks and screenshots, not a claimed human accessibility study, establish the local QA scope.

## CRO hypotheses and measurement

Hypothesis 1: a direct ML-coding headline plus actual receipt preview will make the product easier to understand than the earlier slogan. Hypothesis 2: replay first reduces friction before asking for live access. Hypothesis 3: explicit limits/provenance improve reviewer confidence compared with ambiguous claims. No percentage improvement is asserted and no visitor tracking was added. During judging, observe whether reviewers can reach the verified receipt, interpret the rejection, find live access instructions and recover from a limit. Any later A/B or analytics work needs an explicit privacy/data plan and a real sample; judge clicks are not proof of market conversion.

The CRO review applies clarity, one core outcome, message consistency, objections, hierarchy, cognitive load, trust, specificity, section justification, commitment-matched CTAs, scannability, progressive confidence and empirical claims. Large-customer tactics from generic CRO references were intentionally not adopted because this product has no verified customer base.


## Glass visual refinement (October 30, 2026)

The supplied Abide reference informs the material treatment: a softly lit sage/teal canvas, frosted navigation, translucent panels, bright edge highlights and restrained shadows. AutoML Autopilot retains its existing typography, content, navigation and evidence hierarchy.

The palette uses mist #edf3ef, pine #173a35, teal #146555, warm reflected light #e9dfc9 and opaque reading surfaces #fafcf9. Dark mode uses deep forest #102723 and mint #9bdec3. Glass is applied to the landing instrument, major cards, navigation and workspace panels; code blocks, forms and verdict colors retain readable surfaces. There is no continuous background animation.

Blur is feature-detected, with opaque fallback surfaces and a reduced-transparency preference. Mobile uses smaller header margins without changing target sizes. Existing keyboard, responsive and axe checks validate both themes; screenshots are reviewed separately because axe cannot fully evaluate blended glass contrast.
