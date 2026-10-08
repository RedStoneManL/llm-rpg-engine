# Active scene and historical event provenance

A native ferry episode exposed a deterministic projection error: T14 narrated
in scene `s6`, then the normal summary of an older scene `s4` was appended with
the current day. The envelope-based metadata fold selected `s4` as the current
scene, although `scene_no=6` and the player's physical deck location were intact.
Subsequent director events and the recorded input inherited that stale label.

After the first applied `scene_advanced`, SceneSystem now owns `meta.scene`,
`scene_no`, and `scene_anchor`. Other event scene fields remain provenance.
The projection still applies every event and retains its original timeline
entry. Day retains its independent nondecreasing fold. Before any applied
boundary, legacy envelope-based scene selection is unchanged; replay/rewind
before that boundary naturally restores the legacy behavior.

The repair-outcome preview uses the same metadata helper. Newly emitted director
events use projected current scene/day, rather than the raw tail event or pacing
label. This does not change the director's existing pacing calculation: its raw
scene-label counting is a separate known consumer issue.

This is future context correction, not historical data rewriting. In particular,
the old recorded input's `committed_at.scene=s4` remains unchanged. A malformed
historical `scene_advanced` can still change scene independently of its day;
normal managed turns emit boundaries at the current projected day.

## Evidence

The exact 191-event native trace was replayed with the published implementation
and this change. Final scene changes from `s4` to `s6`; all system states and the
entire timeline compare equal. Every prefix from narration seq186 through input
seq191 remains `s6` after the fix. Replay before the first boundary is identical.
This is deterministic verification on actual gameplay events, not another model
playthrough or a claim about general narration quality.

The original three-action ferry segment completed boarding, crossing, and
arrival, with 12 native GPT-6 Astra responses and four real research-tool calls.
The player remained aboard after arrival; actual disembarkation was still a
separate action. A second, independent navigation defect was retained: the
narrated gangplank had no traversable map edge. That contract is not changed here.

A fresh native continuation from the exact T14 save then executed the same
independent player's voluntary disembarkation: three model responses, one real
map query, no repair and no new summary in that turn. Foreground narration and
director events use `s6`; the legitimate movement to `far_bank_landing` advances
to `s7`. New input provenance records requested scene `s6` and committed scene
`s7`, matching physical arrival ashore. This single continuation verifies fresh
recording and normal advancement; replay supplies the historical-summary check.
