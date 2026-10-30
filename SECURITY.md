# Security reporting

Do not disclose live secrets, private repository content, holdout labels or exploit payloads in public issues. If GitHub private vulnerability reporting is enabled, use the repository Security tab. If it is unavailable, contact the maintainer privately through the owner contact in the hackathon submission; no unverified security email address is published here. For a non-sensitive documentation issue, a public issue is appropriate.

Supply a sanitized reproduction, affected commit/component, observed impact and scope. Use only infrastructure and synthetic data you are authorized to test. Never test against other users' repositories, cloud accounts or the production demo. There is no bug bounty or guaranteed response window.

The tested Docker runner trusts its host daemon and approved dependency image. It is not a microVM or kernel-exploit guarantee. Nebius sandbox integration remains pending. Independent receipts establish declared protocol results, not arbitrary safety. See [operations](docs/V5_OPERATIONS.md) for trust boundaries and remaining hard disk quota requirements.
