# Narrative source provenance

Raw narration and summaries are authored historical records, not current
canonical state. The shared narrative-evidence reader now carries that category
through raw/summary PUSH context and recompression. This adds no model call and
does not add a recap retrieval corpus.

Each projected bucket has the first narration event ID as its stable identity,
plus exact source narration references and original turn/day metadata. New
summary events target that identity, so repeated scene labels are not enough to
select a visit. Old events retain their previous scene-based replay behavior.
An invalid explicit bucket/reference never selects a different record by fallback.

New summaries persist a bounded host-built evidence sidecar. Original source
ranges and summary-creation event time are separate. Recompression unions source
dependencies and retains unknown/partial/truncated coverage, including when
bounds omit entries. Ranges describe known extrema, not continuous coverage of
all intervening events. Legacy summaries can recover bucket association from
the event stream; this does not verify the old compressor's complete lineage.
Neither original prose nor existing summary text is rewritten.

Actor linkage is verified against the original player source but actor IDs,
private input, identity tables and private attributes are not copied into the
shared sidecar. Missing or ambiguous linkage stays unknown. Where linkage is
sound, optional typed custody comes from the projection ending at the exact
`narration_recorded` event, before backstage callbacks. A narration envelope day
that differs from that checkpoint's maximum day gets no typed state evidence.

Custody requires a unique matching canonical/POV relation, safe publicly named
or explicitly published endpoints, and effective public visibility under the
existing `held_by_visibility` contract. Omission follows the documented legacy
public convention and is labeled separately; explicit unknown, malformed,
hidden or secret metadata is not widened. Typed rows are endpoint evidence,
not proof of a transfer, every sentence, a historical interval, or NPC knowledge.
Current canonical inventory remains separately authoritative.

## Actual source experiment

Three fresh Astra responses used production summary/recompression functions on
unchanged recorded native text. This was diagnostic compaction, not new gameplay
or a naturally reached compaction threshold.

- The legacy cabinet paragraph is narration turn 12/day 3, while its new summary
  was created at turn 14. With no original player-input source, it remains actor
  unknown and has no typed custody evidence. The new output attributes the
  cabinet statement to the keeper instead of inventing a verified actor/state.
- The real integrated-play turn 8 return has valid original linkage. Its sidecar
  preserves the historical public-default jade-seal→keeper custody endpoint.
- Recompression of a selected subset retains source range 1–8, three unknown
  sources and one bound source, with partial coverage. The subset omitted the
  errand-completion record; the model's unresolved-errand wording is therefore
  not a full-campaign recall verdict. No broader gameplay improvement is claimed.

All raw paragraphs remained byte-identical. The final day guard leaves both
captured real packets unchanged. Focused host checks covered invalid references,
mixed-day omission and restrictive explicit visibility on copies of the source
events; they are not additional model-play results. No DeepSeek call or broad
offline suite was run. Compression can still lose or misstate information:
source metadata does not prove a summary's semantic correctness.
