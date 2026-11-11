// Run upstream state tests without host UI compatibility shims; sources stay unmodified.
export default { test: { maxWorkers: 1, fileParallelism: false, testTimeout: 30000, hookTimeout: 30000 } };
