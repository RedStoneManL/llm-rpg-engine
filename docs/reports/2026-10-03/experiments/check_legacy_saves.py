"""Compare old/current projections from SQLite backups; original saves read-only."""
import json, sys, sqlite3, subprocess, tempfile, hashlib
from pathlib import Path

OUT=Path(__file__).resolve().parents[1]

if len(sys.argv)>1:
    sys.path.insert(0,sys.argv[1])
    from app.engine import build_engine
    from llm.provider import FakeLLMProvider
    engine=build_engine(sys.argv[2],provider=FakeLLMProvider())
    graph=engine.world['systems']['ontology']
    # Compare authoritative entity/fact/relation history, independent of derived
    # indexes, recap chunking and newly introduced metadata fields.
    value={'entities':{k:vars(v) for k,v in graph.entities.items()},
           'facts':[vars(f) for f in graph.facts], 'relations':[vars(r) for r in graph.relations]}
    print(json.dumps(value,sort_keys=True,ensure_ascii=False));engine.store.close()
    raise SystemExit

results=[]
for source in sorted(Path('/root/games').glob('play*/events.db')):
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    values=[]
    for root in [OUT/'baseline-implementation',Path('/root/rpg-engine-app')]:
        folder=Path(tempfile.mkdtemp(prefix='legacy-copy-',dir=OUT/'experiments'))
        original=sqlite3.connect(f'file:{source}?mode=ro',uri=True)
        copied=sqlite3.connect(folder/'events.db');original.backup(copied);copied.close();original.close()
        text=subprocess.check_output([sys.executable,__file__,str(root),str(folder)],text=True)
        values.append(json.loads(text))
    results.append({'save':source.parent.name,'equivalent':values[0]==values[1],
                    'facts':len(values[1]['facts']),'entities':len(values[1]['entities']),
                    'original_hash_unchanged':before==hashlib.sha256(source.read_bytes()).hexdigest()})
(OUT/'evidence/legacy-replay-implemented.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False))
