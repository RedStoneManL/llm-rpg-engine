// Admission probes execute pinned upstream code against independent Chinese fixtures.
// Runner success validates measurements; each observation has its own admission gate.
import { afterAll, afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtemp, mkdir, rm, writeFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { createSessionStore } from '@candidate/state';
import { recall, DEFAULT_MEMORY_POLICY } from '@candidate/recall';
import { sharedContext } from '@candidate/context';
import { shippedText } from '@candidate/library';
import { materialOf, proposalProblems, editOf } from '@candidate/reflection';
import { MemoryStore } from '@candidate/tavern-memory';
import { WorkspaceFs } from '@candidate/tavern-fs';
import { Wal } from '@candidate/tavern-wal';
import { snapshotStory, newStoryId, storyRoot, listStories } from '@candidate/tavern-story';
import * as expansion from '@candidate/tavern-expand';

const DAY = 86400000;
const START = Date.UTC(2026, 0, 1);
const observations = [];
const roots = [];
const stores = [];
function record(id, observation) { observations.push({ id, ...observation }); }
async function temp() { const r = await mkdtemp(join(tmpdir(), 'rpg-dsh-probe-')); roots.push(r); return r; }
function store() {
  const s = createSessionStore(':memory:', { id: 'independent-probe', name: '雾港', world: { id: 'w', name: '雾港' } });
  stores.push(s);
  s.clock.start('simulated', START);
  for (const [sourceId, name] of [['a','阿岚'],['b','阿禾']]) {
    s.characters.add({ sourceId, name, aliases: [], kind: 'person', tier: 'principal',
      role: 'npc', control: 'agent', summary: '镇民', personality: '谨慎', appearance: '灰衣',
      background: sourceId === 'a' ? 'A_PRIVATE_BACKGROUND' : 'B_PRIVATE_BACKGROUND',
      speech: { style: '自然简短', examples: [] } });
  }
  return s;
}
const actor = (s, key) => s.characters.getBySourceId(key);
function event(s, turn, truth, content, who = 'a', importance = 3, gist = content) {
  return s.edit({ turn, events: [{ turn, round: 1, text: truth, importance, tags: [], involves: [],
    perceivedBy: [{ character: who, mode: 'witnessed', content, gist }] }] }, { seal: true }).eventIds[0];
}
function source(s, variant = 'full') {
  return { events: { perceivedBy(id) {
    const entries = variant === 'shared' ? s.characters.list().flatMap(c => s.events.perceivedBy(c.id)) : s.events.perceivedBy(id);
    return variant === 'perception_only' ? entries.map(x => ({ ...x, event: { ...x.event, text: x.perception.content } })) : entries;
  } }, memories: s.memories, clock: s.clock, relationships: s.relationships };
}
function ask(s, situation, overrides = {}, variant = 'full', who = 'a') {
  return recall(source(s, variant), { characterId: actor(s, who).id, characters: s.characters.list(),
    situation, turn: 999, explain: true, policy: { ...DEFAULT_MEMORY_POLICY, ...overrides } });
}
function prompt(s, memories, situation) {
  const member = key => ({ key, character: actor(s, key) });
  return sharedContext.brief({ member: member('a'), cast: [member('a'), member('b')], situation,
    attempt: 1, turn: 999, language: 'zh', memories,
    text: (key, variables) => shippedText(key, 'zh', variables) });
}
afterEach(async () => {
  vi.restoreAllMocks();
  for (const s of stores.splice(0)) s.close();
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});
afterAll(async () => {
  const dir = process.env.DSH_VALIDATION_RESULTS;
  if (!dir) throw new Error('Set DSH_VALIDATION_RESULTS');
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, 'observations.json'), JSON.stringify({
    evidence_level: 'actual component code + SQLite/filesystem, synthetic inputs; no live LLM or DSH boot',
    model_calls: 0, observations,
  }, null, 2) + '\n');
});

describe('actor scope and counterfactual information boundary', () => {
  it('scoped retrieval keeps another character private; removing repository scoping contaminates the real prompt', () => {
    const s = store();
    for (let n = 1; n <= 12; n++) {
      event(s, n, '世界上存在更多幕后信息', '我搬了第'+n+'箱货物。', 'a');
      event(s, n, '后台密钥 B_SECRET_'+n, '昨天我拿到了密钥 B_SECRET_'+n+'。', 'b');
    }
    const situation = '有人问起昨天的密钥。你知道什么？';
    const rows = [];
    for (const variant of ['full', 'shared']) {
      const memories = ask(s, situation, { flaky: false, recall: 4, associate: 0 }, variant);
      const brief = prompt(s, memories, situation);
      const input = brief.persona + '\n' + brief.prompt;
      rows.push({ variant, foreign_memory_in_prompt: /B_SECRET_/.test(input),
        other_persona_in_prompt: input.includes('B_PRIVATE_BACKGROUND'),
        prompt_utf8_bytes: Buffer.byteLength(input), recalled: memories.map(m => m.source) });
    }
    expect(rows[0].foreign_memory_in_prompt).toBe(false);
    expect(rows[0].other_persona_in_prompt).toBe(false);
    expect(rows[1].foreign_memory_in_prompt).toBe(true);
    record('actor_scope', { gate: 'component_pass_only', rows });
  });

  it('measures whether hidden truth changes recall with identical actor-visible information', () => {
    const twins = [false, true].map(hidden => {
      const s = store();
      event(s, 1, hidden ? '银月密钥藏在库房暗格。' : '陌生人进出库房。',
        '一个穿灰衣的男子从库房走出来。');
      event(s, 2, '掌柜在大厅喝茶。', '掌柜在大厅喝茶。');
      return s;
    });
    const visible = s => s.events.perceivedBy(actor(s, 'a').id).map(x => ({
      content: x.perception.content, gist: x.perception.gist, mode: x.perception.mode,
      turn: x.event.turn, round: x.event.round, importance: x.event.importance,
      involves: x.event.involves, tags: x.event.tags,
    }));
    expect(visible(twins[0])).toEqual(visible(twins[1]));
    const rows = ['full','perception_only'].map(variant => {
      const results = twins.map(s => ask(s, '掌柜的银月密钥在哪里？', {
        flaky: false, recall: 1, associate: 0, diversity: 0,
      }, variant));
      return { variant, identical_visible_inputs: true,
        same_recall: results[0].map(r=>r.text).join() === results[1].map(r=>r.text).join(),
        results: results.map(r => r.map(x => ({ text: x.text, source: x.source, cues: x.why.cues }))) };
    });
    expect(rows[1].same_recall).toBe(true);
    record('truth_noninterference', { gate: rows[0].same_recall ? 'component_pass_only' : 'blocked',
      criterion: 'Changing only DM-private truth must not change NPC recall', rows });
  });

  it('records that a private fact supplied in shared situation survives persona isolation', () => {
    const s = store();
    const clean = prompt(s, [], '两人在酒馆谈话。');
    const contaminated = prompt(s, [], '两人在酒馆谈话。幕后事实：SITUATION_SECRET_71。');
    expect(JSON.stringify(clean)).not.toContain('SITUATION_SECRET_71');
    expect(JSON.stringify(contaminated)).toContain('SITUATION_SECRET_71');
    record('shared_situation_contract', { gate: 'requires_upstream_input_gate',
      note: 'The real prompt builder trusts situation input. This is a component contract, not proof the complete plugin leaks.' });
  });
});

describe('memory policy ablation', () => {
  it('compares default decay, no decay and recent-window over fixed story-time gaps', () => {
    const rows = [];
    for (const days of [0,7,30,90,180]) for (const [key, query] of [
      ['literal','通行证放在哪里？'], ['paraphrase','进城凭证收在哪儿？'],
    ]) {
      const s = store();
      const literal = '旧铜盒最底层有通行证，取出时别弄坏蓝色封签。';
      event(s, 1, literal, literal, 'a', 3, '记得以前处理过通行手续。');
      for (let n = 2; n <= 21; n++) event(s, n, '大厅第'+n+'次清扫。', '大厅第'+n+'次清扫。', 'a', 3);
      s.clock.resync('simulated', START + days * DAY, 999);
      const target = s.events.perceivedBy(actor(s,'a').id)[0].perception.id;
      for (const variant of ['default','no_decay','recent_window']) {
        const results = variant === 'recent_window'
          ? s.events.perceivedBy(actor(s,'a').id).slice(-4).map(x=>({ text:x.perception.content, source:{kind:'perception',id:x.perception.id}, clarity:'clear' }))
          : ask(s, query, { recall:4, associate:0, ...(variant==='no_decay'?{flaky:false}:{}) });
        rows.push({ days, query_type:key, variant,
          target_retrieved:results.some(x=>x.source.kind==='perception'&&x.source.id===target),
          exact_wording:results.some(x=>x.text===literal),
          result:results.map(x=>({text:x.text,clarity:x.clarity})) });
      }
    }
    expect(rows).toHaveLength(30);
    expect(rows.filter(x=>x.variant==='recent_window').every(x=>!x.target_retrieved)).toBe(true);
    record('memory_policy', { gate:'policy_specific_no_blanket_adoption', rows });
  });
});

describe('belief validators and state authority', () => {
  it('compares proposal guard and real write guard, including unrelated valid citations', () => {
    const rows = [];
    for (const label of ['supported','unknown_reference','foreign_reference','unrelated_reference']) {
      for (const skipGuard of [false,true]) {
        const s=store();
        event(s,1,'掌柜答应归还押金后拒绝承认。','掌柜答应归还押金后拒绝承认。');
        event(s,2,'我带来收据，掌柜又换了一种说法。','我带来收据，掌柜又换了一种说法。');
        event(s,3,'阿禾私下获知门后口令。','B_PRIVATE_CODE', 'b',4);
        const a=actor(s,'a'), b=actor(s,'b');
        const seen=s.events.perceivedBy(a.id);
        const material=materialOf(s,1).find(x=>x.character.id===a.id);
        const shown={ perceptions:new Map(material.fresh.map(x=>[x.perception.id,x])),
          beliefs:new Map(material.beliefs.map(x=>[x.id,x])) };
        const ids=label==='unknown_reference'?[999999]
          :label==='foreign_reference'?[s.events.perceivedBy(b.id)[0].perception.id]
          :seen.map(x=>x.perception.id);
        const proposal={ add:[{kind:'belief',text:label==='unrelated_reference'
          ?'井底藏着一条蓝龙。':'掌柜可能不守承诺。',importance:3,about:[],perceptions:ids}],
          revise:[],doubt:[],drop:[] };
        const resolveName=name=>s.characters.findByName(name)[0]?.sourceId;
        const issues=proposalProblems(proposal,shown,resolveName);
        const before=seen.map(x=>x.event.text);
        let accepted=false, writeError=null;
        if(skipGuard||issues.length===0)try{
          s.edit(editOf(4,a,proposal,resolveName));
          accepted=true;
        }catch(e){writeError=e.message;}
        const world_unchanged=JSON.stringify(before)===JSON.stringify(s.events.perceivedBy(a.id).map(x=>x.event.text));
        const other_actor_unchanged=s.memories.list(b.id).length===0;
        expect(world_unchanged).toBe(true);
        expect(other_actor_unchanged).toBe(true);
        rows.push({ label,variant:skipGuard?'skip_proposal_guard':'full',
          proposal_issues:issues,accepted,writeError,world_unchanged,other_actor_unchanged });
      }
    }
    expect(rows.filter(x=>x.label==='supported').every(x=>x.accepted)).toBe(true);
    expect(rows.filter(x=>x.label.includes('reference')&&x.label!=='unrelated_reference').every(x=>!x.accepted)).toBe(true);
    record('belief_evidence',{gate:'mechanical_guards_only_semantic_validation_pending',rows});
  });
});

async function digestTree(root) {
  const fs=new WorkspaceFs(root,null);
  const rows=[];
  for(const path of (await fs.list('')).sort())rows.push([path,await fs.readText(path)]);
  return createHash('sha256').update(JSON.stringify(rows)).digest('hex');
}
async function branchFixture() {
  const root=await temp(),cardRoot=join(root,'card');
  const parentId=newStoryId(),parentRoot=storyRoot(cardRoot,parentId);
  const wal=new Wal(join(parentRoot,'state/wal'));
  const fs=new WorkspaceFs(parentRoot,wal);
  await fs.ensureDir();
  await fs.writeText('story.json',JSON.stringify({version:1,id:parentId,sessionId:'parent',createdAt:'2026-01-01',migrated:false}));
  for(const [floor,body] of [
    ['s#t1','你在河边获得铜钥匙 PAST_TOKEN。'],
    ['s#t2','后来约定在钟楼密会 FUTURE_TOKEN。'],
  ]) {
    await wal.beginFloor(floor);
    await new MemoryStore(fs.withFloor(floor)).write({body});
    await wal.commitFloor(floor);
  }
  const memory=new MemoryStore(fs);
  await memory.mergeBatch(await memory.list(),'你有铜钥匙 PAST_TOKEN，后来约定在钟楼密会 FUTURE_TOKEN。','compress');
  return {root,cardRoot,parentId,parentRoot,fs,wal};
}
describe('branch and derived-memory rollback ablation', () => {
  it('compares full branch, omitted rollback, and omitted summary expansion on real files',async()=>{
    const rows=[];
    for(const variant of ['full','no_rollback','no_summary_expansion']) {
      const f=await branchFixture();
      const parentBefore=await digestTree(f.parentRoot);
      const id=newStoryId();
      const spy=variant==='no_summary_expansion'?vi.spyOn(expansion,'expandAffectedMemories').mockResolvedValue(undefined):null;
      await snapshotStory({cardRoot:f.cardRoot,sourceRoot:f.parentRoot,id,sessionId:'child',includeWal:true,
        prepare:variant==='no_rollback'?undefined:async fs=>{
          await new Wal(join(fs.root,'state/wal')).rollbackFloor('s#t2',fs.root);
        }});
      if(spy)expect(spy).toHaveBeenCalled();
      vi.restoreAllMocks();
      const childRoot=storyRoot(f.cardRoot,id);
      const memory=new MemoryStore(new WorkspaceFs(childRoot,null));
      const active=(await memory.list()).map(x=>x.body);
      const hits=(await memory.search('FUTURE_TOKEN')).map(x=>x.entry.body);
      const parentUnchanged=parentBefore===await digestTree(f.parentRoot);
      expect(parentUnchanged).toBe(true);
      rows.push({variant,parent_unchanged:parentUnchanged,
        future_in_active:active.some(x=>x.includes('FUTURE_TOKEN')),
        future_in_retrieval:hits.some(x=>x.includes('FUTURE_TOKEN')),
        past_preserved:active.some(x=>x.includes('PAST_TOKEN')),
        active,hits});
    }
    expect(rows[0].future_in_active).toBe(false);
    expect(rows[0].future_in_retrieval).toBe(false);
    expect(rows[0].past_preserved).toBe(true);
    expect(rows[1].future_in_active).toBe(true);
    expect(rows[2].future_in_active).toBe(true);
    record('branch_rollback',{gate:'component_pass_only',rows});
  });

  it('does not publish a failed branch or alter the parent',async()=>{
    const f=await branchFixture();
    const before=await digestTree(f.parentRoot),id=newStoryId();
    await expect(snapshotStory({cardRoot:f.cardRoot,sourceRoot:f.parentRoot,id,sessionId:'failed',includeWal:true,
      prepare:async fs=>{
        await fs.writeText('memory/bad.md','partial prepared draft');
        throw new Error('injected_prepare_failure');
      }})).rejects.toThrow('injected_prepare_failure');
    expect(await digestTree(f.parentRoot)).toBe(before);
    expect((await listStories(f.cardRoot)).map(x=>x.id)).toEqual([f.parentId]);
    expect((await readdir(join(f.cardRoot,'stories'))).some(x=>x.startsWith('.preparing-'))).toBe(false);
    record('branch_prepare_failure',{gate:'component_pass_only',parent_unchanged:true,child_published:false,staging_left:false});
  });
});
