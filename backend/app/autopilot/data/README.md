# UCI Adult sample

Becker, B. & Kohavi, R. (1996). **Adult**. UCI Machine Learning Repository.
DOI: https://doi.org/10.24432/C5XW20

Dataset license: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
This attribution and license apply to `adult_sample.csv`, independently of the application's GPL-3.0 license.

The preparation script samples 8,000 rows with seed 2026, renames columns and encodes the target as `above_50k`. See `adult_manifest.json` for archive/sample hashes. The A01 scenario then divides these rows into random reference/current windows and injects an age unit conversion. This is not an observed production incident or a time-series evaluation. The original dataset includes demographic attributes and is used here only for a controlled software experiment.
