# Legacy scope and maintenance decision (DOC-06)

Decision: retain and document the earlier code in place. Do not delete or move it during v5 stabilization. This is reversible and preserves existing imports, notebooks, historical evidence and links. A later extraction/deletion or relicensing decision remains with the owner.

| Surface | Purpose | v5 status |
|---|---|---|
| `/repair`, `backend/app/repair`, `frontend/components/repair` | Repository-native coding and ML verification | Primary supported product |
| `/autopilot`, `backend/app/autopilot` | Data-incident investigation, experiment evidence, historical verifier | Secondary mode; reachable, retains separate semantics |
| Original `/runs`, `/registry`, studio/training orchestration | Dataset-to-model workflow and earlier platform experiments | Retained source, outside the supported v5 demo; some dependencies are not started by the eight-service stack |
| notebooks / earlier architectural notes | Historical explanation and research artifacts | Not proof of the current deployment; retain provenance and dates |

The v5 worker imports selected existing lease/pricing/identity/investigation utilities. Retaining a shared helper does not imply that all original orchestration services are deployed. The hackathon build exposes v5 as the default route. Keep both sources under the existing GPL-3.0 license and preserve third-party notices.

Judge deployment disables provider credentials in the legacy worker. This avoids a secondary live route drawing down the repair evaluation budget. Historical data-incident replays remain inspectable. Do not activate unattended drift schedules or legacy live inference in that deployment.

Maintenance: apply shared correctness/security fixes where the two modes share code; require regression checks for intentional changes. Do not claim feature parity with the original AutoML notebook interface. Link older product descriptions as historical context, not current guarantees. Reorganize the code only after import/deployment impact is reviewed in a separate change.
