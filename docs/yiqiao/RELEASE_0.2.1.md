> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

# YiQiao 0.2.1

[Simplified Chinese](RELEASE_0.2.1.zh-CN.md) | **English**

YiQiao 0.2.1 is a maintenance release focused on exact memory deduplication.

## Changes

- Prevent exact duplicate writes in both inferred and raw (`infer=false`)
  memory paths.
- Normalize Unicode formatting characters and whitespace before computing a
  memory fingerprint.
- Serialize concurrent writes within the same project and entity scope, and
  use deterministic IDs so retries converge on one stored record.
- Check exact fingerprints independently of semantic-search top-k results.
- Add a Dashboard action that keeps the oldest record in each exact duplicate
  group and removes the rest, including graph and feedback cleanup.

Deduplication is limited to exact normalized content within the same
`project_id`, `user_id`, `agent_id`, `app_id`, and `run_id` scope. It does not
delete memories based on fuzzy similarity.

## Upgrade

Pull the `v0.2.1` images or checkout the `v0.2.1` tag, then restart the stack.
Existing exact duplicates can be removed from **Dashboard > Memories > Clean
duplicates** after the upgrade.
