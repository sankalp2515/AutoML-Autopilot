# Two-minute product recording: one browser tab

This is the product footage segment only. Animated opening and ending are separate.

## Recording materials

- Local connected preview: http://localhost:3140/repair
- Sample: Churn service (`churn-service`).
- Task: use the sample-provided description without editing it.
- Contract: retain detected YAML; no uploads, invite codes or external repository required.
- Cached run: `nvidia_churn_verified`, bundled in `frontend/data/repair/generated-replays.json`.
- Original provider: `nvidia_api`; runner: `local-container`; final decision: VERIFIED.
- The original report contains 23 successful model calls, two candidates and 76 persisted report events. Its source report SHA-256 matches the bundled provenance. These are original-run counts, not new model calls made while recording.
- The finalist changes serving to use the shared `FEATURE_ORDER` and adds a train/serve parity test. Do not describe it as retraining, changing the algorithm or deploying a model.

The setup performs real repository staging and contract validation. Fresh sandbox discovery was tested separately; the recording path avoids its measured 52.5-second wait and uses the saved execution's actual test evidence. The cached execution is a separate historical run for the sample, not execution of edited task/contract/evaluation settings. Its existing recording and provider labels must remain visible. No task is created by opening it.

## Exact actions and natural narration

| Time | Action in the same browser tab | Narration |
|---|---|---|
| 0:00–0:10 | Open `/repair`. Click **Try a sample repo**. | “Let me show you how an engineer would investigate a broken ML service. I’ll use our churn-service sample, so we can inspect the code and the evidence together.” |
| 0:10–0:23 | Select **Churn service** (`churn-service`). Click **Continue to contract**. | “The first step is to pin the repository and make the repair boundaries explicit. The agent doesn’t get unrestricted access to change whatever makes the tests pass.” |
| 0:23–0:37 | Keep **Detected YAML** selected. Briefly show the protected tests and commands. Click **Validate and continue**. | “Here are the commands it can execute and the files it may change. Existing tests stay protected. For this sample, we also check that serving and batch predictions agree, and that predictive quality is preserved.” |
| 0:37–0:53 | Keep the sample task unchanged. Show the declared failing-test notice and included sample evaluation pack. Do not click **Discover tests** or **Start repair**. Scroll to **Cached sample execution available**. | “The sample already declares the failing test and includes a separate evaluation pack. A fresh repair would reproduce the failure in the sandbox. For this walkthrough, we’ll inspect that work in the saved execution.” |
| 0:53–1:00 | Click **Open cached sample run**. | “To avoid inference waits in this walkthrough, I’m opening a genuine saved execution of this sample. These are its original results, not a new run of the form we just reviewed.” |
| 1:00–1:20 | Set **Playback speed** to **4×**. Click **Play recording**. Allow it to finish; do not use **Guided tour** or scrub to invent progress. | “Watch the recorded investigation progress through actual tool calls and source edits. The agent traces the serving mismatch, proposes a change, and runs checks. The activity is backed by recorded tool results, rather than a generated status animation.” |
| 1:20–1:36 | Remain on **Activity**. Scroll to **Candidate source & terminal** and show the diff and published output. | “The important change is here: serving now uses the shared feature order. We can inspect the exact edit and the test output. The recording also includes a new parity test to help catch the same mismatch again.” |
| 1:36–1:52 | Click the in-app **Verdict & receipt** tab. Click **Split view** on the diff; scroll between the patch and verification evidence as needed. | “The final report brings the code checks and ML checks together. VERIFIED means this candidate met the recorded checks. The receipt also identifies the provider and isolated runner, so the reviewer can trace where the evidence came from.” |
| 1:52–2:00 | Click the in-app **PR handoff** tab and hold on its real contents. | “The outcome is a patch an engineer can review and hand off. The final merge decision stays with the team.” |

The recording route deliberately does not execute fresh discovery. The optional Discover tests action was verified separately and took 52.5 seconds on this machine. Do not narrate a new test execution on the task form. The saved recording shows actual original tool/test evidence. Playback speed changes presentation timing only.

This original recording uses NVIDIA developer inference. It is not evidence of Nebius Token Factory or Nebius Sandboxes execution. The final hackathon export needs a genuine qualifying Nebius run and accurate narration of the services actually used.

## Prompt for Claude / Clueso preparation

Create an approximately 120-second product walkthrough segment for AutoML Autopilot. This segment will sit between separately produced animated opening and closing scenes. Do not add another intro, logo sequence, outro, music bed or generic marketing montage.

Use actual screen footage supplied by me and the exact one-tab route described above. If the footage is not attached, ask for it before rendering the product segment. Do not generate synthetic product screens, fabricate an agent run, invent code/test output, or use a scripted QA response as model evidence. Do not record or call an external model unless I explicitly request that work.

Treat the narration above as a conversational senior product manager speaking to an engineering stakeholder. Preserve my natural voice where possible: measured, clear and specific, with brief pauses while a result becomes visible. Avoid a robotic list of clicks, exaggerated excitement, unsupported claims, and voiceover that merely reads UI labels. Tighten repeated words without changing meaning or provenance.

Keep one browser tab and one continuous task story. In-app view changes are allowed. Preserve natural cursor movement and short reading pauses. Use a restrained zoom when the pointer reaches the relevant source line or verification result; follow real clicks rather than adding regular or random pulses. Avoid whip pans, bouncing callouts and transitions after every action. The product should remain recognizable and readable.

Retain the cached-execution disclosure, original NVIDIA provider, runner, verdict, limitations and any relevant error. Identify the cached run in narration and briefly on screen. A recording is not new live inference. Do not replace the provider name with Nebius or claim hosting, model deployment, a created pull request or a merge without corresponding evidence.

The key story beats are: inspect a repository; authorize bounded edits; explain the declared failure and separate evaluation pack; inspect genuine sandbox test evidence in the saved agent investigation; show the actual shared-feature-order patch and parity test; inspect verification and receipt; finish with human-controlled handoff.

Use short, minimal annotations only where they explain something the viewer cannot immediately infer. Keep annotations away from source text, terminal output and receipt details. Trim genuine idle waits using clean cuts with a brief “wait shortened” note where needed. Do not invent an execution duration or change measured values.

Deliver a 16:9, 1920×1080 walkthrough segment, the final narration transcript, and a short edit decision list noting speed changes and shortened waits. Aim for 115–125 seconds; check the entire hackathon video remains below three minutes once the separate opening and ending are added. Before final submission, request genuine Nebius execution footage if it has not yet been supplied; leave its claim out rather than substituting a logo or unverified narration.


## Local validation

- Production frontend build and rebuilt Docker frontend image passed.
- All 22 existing workspace browser checks passed.
- The full connected new-user flow, with fresh sandbox discovery, passed. Two playback-layout checks passed at 623 and 920 pixels.
- Measured first full flow: source 5.506 s; intake 4.377 s; validation 0.622 s; fresh sandbox discovery 52.499 s; cached navigation/playback 19.370 s.
- Independent playback check: 61 frames in 18.134 seconds at 4x, followed by receipt and handoff.
- The preview uses same-origin API requests. The Next proxy timeout was increased from its default 30 seconds to the bounded 960-second admission deadline; the running Docker frontend reports 960000 ms. No command/task execution deadline was removed.
- No new inference calls, task creation, video recording or Clueso project creation occurred during these checks.

- Exact recording route (without optional fresh discovery) passed again in a new browser context: 35.7 seconds total, with source 8.873 s, intake 4.051 s, validation 0.489 s and cached navigation/playback 19.168 s. No new task or inference call. Final local readiness: database, worker, verifier and sandbox all OK.
