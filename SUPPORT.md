# Support

This is an early open-source hackathon product. There is no paid support plan or response-time guarantee.

- Search [existing issues](https://github.com/sankalp2515/AutoML-Autopilot/issues) and the [product limits](README.md#results-and-honest-limits).
- For a reproducible bug, open an issue with the commit, OS/browser, route, expected/actual result and sanitized reproduction. Include only a public/synthetic fixture and safe event/receipt references.
- For judge access or an extended live evaluation, use the project owner's contact supplied in the Devpost testing instructions. Never post a judge code, token or private repository in a public issue. Replays remain available without new inference.
- For a suspected vulnerability, follow [SECURITY.md](SECURITY.md).

A 429 means new live work is temporarily limited, not that an existing result disappeared. Respect Retry-After, reopen the existing run and retain the same Idempotency-Key for the same request. A budget/access refusal is explained in the UI; don't create extra identities to evade the shared evaluation allowance. Missing holdout data or infrastructure failures must not be treated as a successful ML verdict.
