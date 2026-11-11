// Candidate aliases resolve to pinned upstream implementations, never substitutes.
import { resolve } from 'node:path';
const lab = process.env.DSH_VALIDATION_UPSTREAM;
if (!lab) throw new Error('Set DSH_VALIDATION_UPSTREAM to the isolated upstream directory');
const a = resolve(lab, 'agentic/packages');
const t = resolve(lab, 'liketavern/src');
export default {
  resolve: { alias: {
    'vitest': resolve(lab, 'node_modules/vitest/dist/index.js'),
    '@dsh-agentic-roleplay/state': resolve(a, 'dsh-roleplay-state/src/index.ts'),
    '@candidate/state': resolve(a, 'dsh-roleplay-state/src/index.ts'),
    '@candidate/recall': resolve(a, 'dsh-roleplay-state/src/memory/recall.ts'),
    '@candidate/reflection': resolve(a, 'dsh-agentic-roleplay-core/src/host/memory/reflection.ts'),
    '@candidate/context': resolve(a, 'dsh-agentic-roleplay-core/src/host/orchestration/defaults.ts'),
    '@candidate/library': resolve(a, 'dsh-agentic-roleplay-core/src/host/prompts/library.ts'),
    '@candidate/tavern-memory': resolve(t, 'state/memory.ts'),
    '@candidate/tavern-wal': resolve(t, 'state/wal.ts'),
    '@candidate/tavern-fs': resolve(t, 'state/workspaceFs.ts'),
    '@candidate/tavern-story': resolve(t, 'state/story.ts'),
    '@candidate/tavern-expand': resolve(t, 'state/memoryRollback.ts'),
  } },
  test: { include: ['*.test.mjs'], maxWorkers: 1, fileParallelism: false, testTimeout: 30000, hookTimeout: 30000 },
};
