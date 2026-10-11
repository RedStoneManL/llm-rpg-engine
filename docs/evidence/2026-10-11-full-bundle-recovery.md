# Full recovery bundle exercise

- Source checkpoint: `472ffde2a67a7e568c808a6530dd61ca9155b2b9`
- Source tree: `8d8b8c132b395a43b604c36ca9c82cac6e224cf4`
- Full bundle SHA-256: `fead3f028fd80062a440413d315ffda6d1ae0512ffeed13a045b9cb49c730e3e`
- Date: 2026-10-11 UTC

The first RCV artifact was deliberately incremental and therefore required the
known `6b54411` base. To prove that the recovery deliverable does not depend on
that environment, this checkpoint also created a complete-history bundle from
`472ffde` and performed a direct clone into a fresh, empty directory:

```text
git bundle create /tmp/llm-rpg-engine-rcv-01-02-recovery-verified.bundle HEAD
git bundle verify /tmp/llm-rpg-engine-rcv-01-02-recovery-verified.bundle
git clone /tmp/llm-rpg-engine-rcv-01-02-recovery-verified.bundle <fresh-dir>
```

The clone completed with a clean detached checkout and exactly matched the
source:

```text
HEAD 472ffde2a67a7e568c808a6530dd61ca9155b2b9
tree 8d8b8c132b395a43b604c36ca9c82cac6e224cf4
status clean
```

No source was copied outside Git, and no remote, model, deployment, or
credential operation occurred.
