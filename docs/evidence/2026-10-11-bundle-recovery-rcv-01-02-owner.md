# RCV-01/02 bundle recovery exercise

- Source checkpoint: `d58822d1d2f936b1f086cbddff25973671b73fa2`
- Source tree: `aabd5669fb5f4b88c142c41a146f918204a17b74`
- Incremental bundle: `/tmp/llm-rpg-engine-rcv-01-02-owner.bundle`
- Bundle SHA-256: `eaeeb4c4690606fee0b50f7df30303c71a5e54f18f5e486cdc329ecfe7792a36`
- Required base: `6b544111a1b0e26fbc232ba0dcb3df33e92b8dbb`
- Date: 2026-10-11 UTC

This is an actual recovery exercise, not only `git bundle verify` output. In a
fresh directory, the repository was cloned without local hard links, detached
at the required base, fetched from the bundle into `recovery/HEAD`, and checked
out at that ref:

```text
git clone --no-local /workspace/llm-rpg-engine <fresh-dir>
git -C <fresh-dir> checkout --detach 6b544111a1b0e26fbc232ba0dcb3df33e92b8dbb
git -C <fresh-dir> fetch /tmp/llm-rpg-engine-rcv-01-02-owner.bundle \
  HEAD:refs/remotes/recovery/HEAD
git -C <fresh-dir> checkout --detach recovery/HEAD
```

The recovered checkout reported:

```text
HEAD d58822d1d2f936b1f086cbddff25973671b73fa2
tree aabd5669fb5f4b88c142c41a146f918204a17b74
status clean
```

Both HEAD and tree exactly matched the source worktree. Because this artifact
is intentionally incremental, it requires the listed `6b54411` base rather
than functioning as a standalone clone source.
