# Secret Scanning

YiQiao scans both the current source tree and the complete public Git history
with Gitleaks 8.28.0. The release history starts with the target repository's
placeholder commit and adds the reviewed YiQiao source as a single snapshot; it
does not merge or retain the upstream repository's commit graph.

The public release contains no `.gitleaksignore`. Every YiQiao commit must pass
without commit, path, rule, or line fingerprint exceptions. The narrow rule in
`.gitleaks.toml` permits only adjacent empty provider-key entries in the checked
in environment template. Supplying either value stops the rule from matching.

Run the release checks with:

```bash
gitleaks dir --redact=100 --no-banner .
gitleaks git --redact=100 --no-banner .
```

Generate a fully redacted report outside the repository when investigating a
failure:

```bash
gitleaks git --redact=100 --no-banner --report-format json \
  --report-path /tmp/yiqiao-gitleaks-review.json .
```

Gitleaks exits with status 1 when it writes a report containing findings. Fix
or remove the detected material; do not add fingerprint exceptions merely to
make CI pass. Runtime state such as `.env`, `server/history`, logs, browser
artifacts, database dumps, and backups is absent from a clean checkout and must
never be committed or allowlisted.
