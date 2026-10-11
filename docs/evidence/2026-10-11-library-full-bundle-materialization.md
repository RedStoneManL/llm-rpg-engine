# Library full-bundle materialization check

- Target Library file: `libfile_4fd52fcdb30c8191bd43f219f4eff69c`, version 0
- Expected size: 2,690,620 bytes
- Local full-bundle SHA-256 at upload time:
  `cfcf34a728118d9a76be378e728b588b14968bac22cdd1a8e857b5aeabebafab`
- Date: 2026-10-11 UTC

The persistent full bundle is present in Library and is distinct from the
earlier 10,358-byte incremental `libfile_e447b2848384819196f9b9fd84e64eae`.
The latter requires `6b54411` and is not the standalone recovery artifact.

To verify bytes from persistent storage, `prepare_materialize` was invoked
twice for the full bundle. Each time it returned the same file id, version,
and expected size with a fresh signed GET URL. Both immediate downloads from
the execution environment received HTTP 403 before a file was created. This
is recorded as a connector/materialization blocker: no SHA or clone claim is
made for bytes fetched back from Library.

Separately, the local source bundle with the listed SHA was directly cloned in
a new empty directory and matched its source HEAD/tree. That verifies bundle
construction only; the pending Library-byte round trip remains required.
