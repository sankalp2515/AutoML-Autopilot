# C2: local container execution

Candidate execution uses the Docker CLI with one copied work directory and one
output directory per opaque environment ID. Runtime containers have no network,
run as UID 10001 with a read-only root, bounded tmpfs, no capabilities, no new
privileges, a PID limit and CPU/RAM limits. `--pull never` also prevents a runtime
call from contacting a registry through the daemon. Only preparation can install
dependencies or obtain a base image.

Preparation creates a platform-owned Dockerfile with an argv-form pip command;
repository Dockerfiles cannot inject build instructions. Images are tagged by a
digest of source paths/bytes, Python version, install command, base-image name
and runner recipe revision. `fork` copies the work/output state. Candidate source
is removed from the final image filesystem after installation; only the attempt's
own source is mounted at runtime. The production base is `python:<version>-slim`.

Subprocess output is drained continuously, retaining at most 4 KiB of head and
12 KiB of tail per stream. A timeout forcibly removes the named container before
killing its client. Uploaded/collected/read/written paths reject traversal,
absolute paths, Windows stream aliases, symlinks, junctions, other reparse points,
special files and hard links. SHA-256 collection hashes complete regular files.
Secret-path names are refused before reading repository content.

## Validation

The focused suite uses real Linux Docker containers through Docker Desktop on
Windows, with a cached trusted Python 3.12 image and an empty offline requirements
file. The explicit operator-only `base_image` override allows this without a
registry/API call. Runtime tests exercise network denial, non-root/read-only
execution, host/sibling isolation, timeout removal, parent/child independence,
uploads, nonzero exits, bounded output, explicit environment values, artifact
hashing, path escapes, Linux-created Windows reparse points and image-cache
invalidation. Tests skip cleanly when the CLI or Linux daemon is unavailable. A
fresh CI host may obtain the official image during preparation. No provider calls
are part of these tests.

The initial run found a Windows-specific reparse-point error. Validation now uses
`lstat` and rejects all reparse points before following or opening them.

Recorded local results: contracts plus the initial runner suite passed 47 tests;
the expanded runner suite passed 27 tests with one Windows-only POSIX-mode skip.
The subsequent ambiguity/timeout/nonzero review suite passed six tests, and the
final preparation-recipe/ambiguous-artifact smoke test passed one test. A separate
network-disabled Linux-container check passed native executable-bit preservation
and mode-sensitive cache identity. Native Linux host UID permissions were
simulated to exercise the real container cleanup fallback.

## Interface proposals and limits

- Frozen `contracts.py` and `sandbox/base.py` are unchanged. Consider formally
  adding `isolated` and the optional `env` keyword to the runner protocol; this
  implementation already supports them without breaking existing calls.
- `work_dir(env_id)` is a trusted host-side integration hook for C3 file tools.
  It must never be serialized into an agent-visible tool result or API response.
- The frozen artifact comment describes `/out`-relative collection while newer
  oracle work uses work-relative `_ap/` paths. Collection/read currently try the
  work directory or `/out` when unique; duplicate names in both fail closed with
  `SandboxError`. Standardize the meaning in a reviewed interface revision.
- The frozen prepare signature does not receive the admission snapshot digest.
  The runner computes its own deterministic path/content digest for cache keys;
  admission-record binding remains the platform's responsibility.
- The daemon and operator-selected base image are trusted. These are shared-kernel
  containers, not microVMs. The C12 daemon service/credential separation is outside
  this card. Native Linux UID-owned directories are cleaned with a fallback
  command as UID 10001 inside the same runtime jail before host-side removal;
  Windows Docker Desktop and that fallback's recovery path were tested locally.
- Repository packaging support is deliberately limited: removing `/build` breaks
  editable installs, and a source-installed `src`-layout package may import frozen
  site-packages instead of edits in `/work/src`. The C1 flat-package,
  requirements-only execution path is supported. Do not claim arbitrary package
  installation layouts; review an explicit `/work` installation/import mapping
  or execution-contract extension before supporting those repositories.
- Image tags persist for reuse; `discard` removes environments, not shared images.
  Operator cache retention and aggregate storage quotas remain later work.
- No push or remote CI execution was performed. The cached-image override tests
  runtime isolation and offline preparation, not a fresh public Python-image pull
  or a network dependency installation.
