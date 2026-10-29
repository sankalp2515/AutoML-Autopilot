# B14: pricing-model

Make scoring handle a zero list price without changing valid predictions.

Unpatched must fail its declared visible test; wrong_fix must pass visible checks and fail train_serve_parity; reference_fix must pass both oracles.

All rows are synthetic. These proofs exercise the checkers, not autonomous-agent accuracy or a fairness certification.
