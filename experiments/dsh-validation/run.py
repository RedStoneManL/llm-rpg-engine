#!/usr/bin/env python3
"""Reproduce pinned third-party component tests and admission probes without changing runtime code."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PLAN = json.loads((HERE / "plan.json").read_text())
STATE_TESTS = ["tests/memory/recall.spec.ts", "tests/sqlite/knowledge.spec.ts", "tests/sqlite/recall-index.spec.ts"]
CORE_TESTS = ["tests/host/memory/reflection.spec.ts", "tests/host/timeline/branch.spec.ts"]
TAVERN_TESTS = ["test/memoryRollbackGraph.test.ts", "test/memoryRollbackHumanEdit.test.ts",
                "test/walIntegrity.test.ts", "test/walCrashMatrix.test.ts", "test/memoryRecallHoldout.test.ts"]

def read_command(args, cwd):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()

def setup(lab):
    lab.mkdir(parents=True, exist_ok=True)
    for name, pin in PLAN["upstream"].items():
        path = lab / name
        if not (path / ".git").exists():
            path.mkdir(parents=True, exist_ok=True)
            if any(path.iterdir()):
                raise RuntimeError(f"Refusing nonempty checkout destination: {path}")
            subprocess.run(["git", "init", "-q", str(path)], check=True)
            subprocess.run(["git", "fetch", "--depth", "1", "https://github.com/" + pin["repository"] + ".git",
                            pin["commit"]], cwd=path, check=True)
            subprocess.run(["git", "checkout", "--detach", "-q", "FETCH_HEAD"], cwd=path, check=True)
    verify(lab)
    locked = HERE / "runner-dependencies"
    for filename in ("package.json", "package-lock.json"):
        destination = lab / filename
        if destination.exists() and json.loads(destination.read_text()) != json.loads((locked / filename).read_text()):
            raise RuntimeError(f"Refusing to overwrite a different dependency manifest: {destination}")
        shutil.copyfile(locked / filename, destination)
    subprocess.run(["npm", "ci", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=lab, check=True)
    subprocess.run(["npx", "--yes", "pnpm@12.3.4", "install", "--frozen-lockfile", "--ignore-scripts",
                    "--filter", "@dsh-agentic-roleplay/core...", "--filter", "@dsh-agentic-roleplay/state..."],
                   cwd=lab / "agentic", check=True)

def verify(lab):
    result = {}
    for name, pin in PLAN["upstream"].items():
        path = lab / name
        head = read_command(["git", "rev-parse", "HEAD"], path)
        dirty = read_command(["git", "status", "--porcelain", "--untracked-files=no"], path)
        if head != pin["commit"] or dirty:
            raise RuntimeError(f"Upstream {name} must be clean at {pin['commit']}; got {head}, dirty={bool(dirty)}")
        result[name] = {**pin, "tracked_files_clean": True}
    return result

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upstream-root", type=Path, required=True)
    ap.add_argument("--results", type=Path, default=REPO / "docs/verification/dsh-validation-round1")
    ap.add_argument("--python", default=sys.executable, help="Interpreter with our engine's dependencies")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--groups", default="agentic-state,agentic-core,liketavern-state,liketavern-retrieval,probes,own")
    args = ap.parse_args()
    lab, results = args.upstream_root.resolve(), args.results.resolve()
    if args.setup:
        setup(lab)
    pins = verify(lab)
    results.mkdir(parents=True, exist_ok=True)
    files = {
        "agentic_license": lab / "agentic/LICENSE",
        "tavern_license": lab / "liketavern/LICENSE",
        "agentic_recall": lab / "agentic/packages/dsh-roleplay-state/src/memory/recall.ts",
        "agentic_context": lab / "agentic/packages/dsh-agentic-roleplay-core/src/host/orchestration/defaults.ts",
        "agentic_reflection": lab / "agentic/packages/dsh-agentic-roleplay-core/src/host/memory/reflection.ts",
        "tavern_wal": lab / "liketavern/src/state/wal.ts",
        "tavern_memory_rollback": lab / "liketavern/src/state/memoryRollback.ts",
        "tavern_story": lab / "liketavern/src/state/story.ts",
        "independent_probes": HERE / "probes.test.mjs",
        "experiment_plan": HERE / "plan.json",
        "own_boundary": HERE / "test_own_boundary.py",
        "runner": HERE / "run.py",
        "probe_config": HERE / "vitest-probes.config.mjs",
        "state_config": HERE / "vitest-state.config.mjs",
    }
    provenance = {"upstream": pins, "files_sha256": {k: digest(v) for k, v in files.items()},
                  "node": read_command(["node", "--version"], REPO),
                  "python": read_command([args.python, "--version"], REPO),
                  "our_commit": read_command(["git", "rev-parse", "HEAD"], REPO),
                  "live_model_calls": 0, "dsh_boot_tested": False,
                  "platform": sys.platform, "setup_scripts": "disabled; component testing only"}
    if (lab / "package-lock.json").exists():
        provenance["runner_dependency_lock_sha256"] = digest(lab / "package-lock.json")
    (results / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    if args.verify_only:
        print(json.dumps(provenance, indent=2))
        return 0
    common = ["--reporter=default", "--reporter=json"]
    def vt(cwd, executable, tests, config=None):
        return [str(executable), "run", *(["--config", str(config)] if config else []), *tests,
                "--maxWorkers=1", *common], cwd
    a = lab / "agentic/packages"
    jobs = {
        "agentic-state": vt(a / "dsh-roleplay-state", a / "dsh-roleplay-state/node_modules/.bin/vitest", STATE_TESTS),
        "agentic-core": vt(a / "dsh-agentic-roleplay-core", a / "dsh-agentic-roleplay-core/node_modules/.bin/vitest", CORE_TESTS),
        "liketavern-state": vt(lab / "liketavern", lab / "node_modules/.bin/vitest", TAVERN_TESTS, HERE / "vitest-state.config.mjs"),
        "liketavern-retrieval": vt(lab / "liketavern", lab / "node_modules/.bin/vitest",
                                 ["test/memoryRecallEval.test.ts"], HERE / "vitest-state.config.mjs"),
        "probes": vt(HERE, lab / "node_modules/.bin/vitest", [], HERE / "vitest-probes.config.mjs"),
        "own": ([args.python, "-m", "pytest", "-o", "addopts=", "-q",
                 "experiments/dsh-validation/test_own_boundary.py",
                 "tests/app/test_history_sources.py", "tests/app/test_explicit_speech.py",
                 "--junitxml=" + str(results / "own-boundary.xml")], REPO),
    }
    selected = [s.strip() for s in args.groups.split(",") if s.strip()]
    unknown = set(selected) - jobs.keys()
    if unknown:
        raise ValueError(f"Unknown groups: {unknown}")
    env = {**os.environ, "DSH_VALIDATION_UPSTREAM": str(lab), "DSH_VALIDATION_RESULTS": str(results),
           "PYTHONPATH": str(REPO)}
    runs = []
    for name in selected:
        cmd, cwd = jobs[name]
        if name != "own":
            cmd = [*cmd, "--outputFile=" + str(results / ("probes-tests.json" if name == "probes" else name + ".json"))]
        start = time.monotonic()
        with (results / (name + ".log")).open("w") as log:
            completed = subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        row = {"group": name, "exit_code": completed.returncode,
               "elapsed_seconds": round(time.monotonic() - start, 3), "log": name + ".log"}
        runs.append(row)
        print(json.dumps(row), flush=True)
    verify(lab)
    (results / "last-run.json").write_text(json.dumps(runs, indent=2) + "\n")
    return int(any(r["exit_code"] for r in runs))

if __name__ == "__main__":
    raise SystemExit(main())
