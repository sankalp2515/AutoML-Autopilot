# Submission inputs and remaining ship gates

Updated October 30, 2026. No cloud resources were provisioned during the glass UI update.

## Inputs needed from the owner

1. Link Google Cloud project utoml-autopilot to the billing account holding the  trial credit. The active gcloud project was verified; gcloud billing projects describe automl-autopilot returned illingEnabled: False. Confirm trial expiry and available credit in Billing. Do not paste account tokens into chat.
2. Provide the Nebius Token Factory key through private local configuration and the sandbox-enabled project ID. Keep credentials outside Git. AI Cloud VM credentials are unnecessary for the planned Google Cloud host.
3. Choose a public domain or approve a temporary HTTPS hostname, and confirm the desired Google Cloud region and VM size against the cost plan. Provisioning remains a separate deployment step.
4. Supply a private judge invite/access method and an owner contact for access recovery. Share these privately through submission testing instructions.
5. Provide the under-three-minute demo video, final submission description, significant-update statement and genuine provider feedback after using the integration.

## Engineering gates before claiming submission readiness

- Implement and test the direct Nebius Sandboxes adapter; the existing remote runner talks to our Docker service.
- Verify Token Factory model IDs, successful NVIDIA model calls and evidence receipts on the actual project.
- Validate repository, filesystem, network and sealed-verifier boundaries on the selected runtime; finish disk quota enforcement for the deployment target.
- Deploy HTTPS, backups, restore checks, health monitoring and the persistent judge-only admission policy; run the complete public judge flow from a fresh browser.
- Review remaining benchmark limitations honestly. Do not describe authored checker proofs as agent success rates or a partial comparison as a complete study.
- Publish the reviewed source and required submission assets only under the owner-authorized release workflow.

The local Application Default Credentials quota-project warning is distinct from project billing linkage. Align its quota project when a deployment tool actually uses ADC; it does not supply billing credit or enable billing.

## Glass UI validation

The production frontend build passed, and all 27 existing landing/workspace browser scenarios passed, including light/dark desktop/mobile axe checks, keyboard navigation and judge-limit retry behavior. Desktop light and mobile dark screenshots were visually reviewed. No provider calls or cloud resources were used.

Docker status and the existing localhost:3135 preview stalled during refresh, so the refresh was stopped. The separate localhost:3140 frontend preview is for visual review and recorded evidence; it does not establish backend or cloud readiness. The full stack must be rechecked after Docker is responsive.
