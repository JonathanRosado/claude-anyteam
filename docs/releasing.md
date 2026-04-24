# Releasing

Maintainer-facing notes for shipping new versions of `claude-anyteam` to both npm and PyPI via the `.github/workflows/release.yml` GitHub Actions pipeline.

## One-time setup

Both registries authenticate via long-lived tokens stored as repo secrets. Configure them once:

1. **npm access token** — https://www.npmjs.com/settings/<your-username>/tokens → **Generate New Token** → type **Automation** (works with 2FA). Save as repo secret `NPM_TOKEN` at https://github.com/JonathanRosado/claude-anyteam/settings/secrets/actions.
2. **PyPI API token** — https://pypi.org/manage/account/token/. For the very first release the token must be scoped to **"Entire account"** because the project doesn't exist on PyPI yet and you can't scope a token to a project that isn't there. After the first release, revoke the entire-account token and issue a new one scoped to project `claude-anyteam`. Save as repo secret `PYPI_API_TOKEN`.

## Per-release

1. Bump version in both files (must match, the release workflow will publish whatever it reads):
   - `npm/package.json` — `"version": "X.Y.Z"`
   - `pyproject.toml` — `version = "X.Y.Z"`

2. Commit and push the version bump to `main` via your normal PR flow.

3. Tag and push:
   ```bash
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

4. The `release.yml` workflow fires on the `v*` tag push:
   - `test` job runs first (`uv run pytest -q`); if any test fails, publishes are skipped.
   - `publish-npm` and `publish-pypi` run in parallel once tests pass.

5. Watch the run at https://github.com/JonathanRosado/claude-anyteam/actions. Expected duration: ~2 minutes end-to-end.

## Verifying

```bash
# npm side: install the freshly-published version
npx --yes claude-anyteam@X.Y.Z --help

# PyPI side: install via uv
uv tool install --reinstall claude-anyteam==X.Y.Z
```

## Caveats

- **PyPI versions cannot be re-uploaded.** If a bug ships in `vX.Y.Z`, yank it (hides the version from `pip install` without an explicit `==X.Y.Z`) and release `vX.Y.(Z+1)`. You can't overwrite.
- **npm is similar** — published versions can be deprecated but the tarball stays addressable forever.
- **Version parity across files is load-bearing.** If `npm/package.json` says `0.3.0` and `pyproject.toml` says `0.2.9`, the two registries end up with mismatched versions and `npx claude-anyteam` will try to `uv tool install claude-anyteam` at the latest PyPI version (0.2.9), silently missing any 0.3.0 features.
- **npm's `--provenance` flag** in the workflow attaches a cryptographic attestation linking the published tarball to this commit. Requires the repo to be public (it is) and the workflow to have `id-token: write` (added via default permissions). If you see a provenance error, drop the flag and re-release.
- **Tokens in chat.** The `PYPI_API_TOKEN` originally used for v0.3.0 was pasted in chat during development. Rotate it on PyPI after the first successful release and re-issue scoped to project.
