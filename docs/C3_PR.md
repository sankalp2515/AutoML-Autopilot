# C3: pinned repository intake and deterministic tools

Repository admission clones a local Git directory or credential-free HTTPS URL,
materializes an exact 40-character commit SHA, parses `autopilot.yaml`, and returns
the tracked-file map, declared entrypoints and a digest over paths, Git modes and
file contents. Local untracked changes are excluded. Sensitive tracked names,
symlinks, submodules, case collisions and nonportable Windows paths are rejected.
Existing snapshot destinations are never overwritten. Git hooks, external diff,
text conversion, global configuration and interactive credentials are disabled.

`ToolExecutor` executes the frozen `AgentStep` protocol. Reads have numbered,
200-line ranges; literal searches stop at 50 hits. Edits require one exact match
or a genuinely new file and enforce ECC roots, glob immutability, configuration
protection and immutable existing tests, including Windows case aliases. Symlink,
junction, hardlink and binary escape attempts fail closed. All command execution
uses the declared argv through `SandboxRunner`; there is no host-run fallback.
Denials include a rule and a recovery hint.

## Integration

```python
snapshot = intake_repository(source, exact_sha, snapshot_directory)
executor = ToolExecutor(
    local_runner.work_dir(env_id), contract=snapshot.contract, ecc=ecc,
    runner=local_runner, env_id=env_id,
)
result = executor.execute(agent_step)
diff = platform_diff(
    local_runner.work_dir(env_id), exact_sha,
    snapshot_dir=snapshot.directory,
)
```

The caller must serialize file tools with candidate execution and must use the
actual candidate environment directory, never the pristine snapshot. The local
runner's host-only work-directory accessor is not agent/API output.
`platform_diff` uses trusted external Git metadata and its own temporary index;
it includes new source files without mutating the pristine snapshot's index.
Git metadata/history must not be copied into the candidate sandbox.

## Frozen-interface proposals (not implemented here)

- Add versioned, bounded `list_files/stat` filesystem operations to the runner
  protocol so tools can support a remote sandbox without a host-directory getter.
- Represent commands as typed argv templates in a future contract version.
  This implementation tokenizes first and substitutes only known platform paths;
  malformed placeholder expressions are denied rather than interpreted.
- C11 should enforce admission limits across clone transport/history, disk,
  concurrency and retention. Current tracked-file limits are not transport quotas.

Neither `contracts.py` nor `sandbox/base.py` was changed.

## Validation

Run from `backend`:

```sh
python -m pytest tests/test_repair -p pytest_asyncio.plugin -q
```

Local Windows results: **97 passed** for the repair suite, followed by **1 passed**
for the added junction-guard test. `git diff --cached --check` passed. The suite
reports a Python 3.12 `shutil.rmtree(onerror=...)` deprecation warning; cleanup
still executes correctly. Frontend and broader backend checks are reported by
the parent task against the shared base, not claimed as C3-specific runs here.

Coverage includes pinned SHA stability, untracked exclusion, cleanup, malformed
contracts, entrypoint discovery, external-index diffs, path traversal, reserved
Windows names, alternate stream syntax, short names, case tricks, symlink/junction
guards, hardlinks, test/config protection, binary edits, exact-match rules,
empty-old overwrite denial, read/search bounds, command selectors, and mutated
contract rejection. Tests use local temporary Git repositories only; no model or
web API is called. HTTPS transport and remote CI are not exercised here.
