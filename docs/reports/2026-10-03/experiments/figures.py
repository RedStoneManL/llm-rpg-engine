from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'figures';DEST.mkdir(exist_ok=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.spines.bottom':False,'axes.labelcolor':'#4d575e','xtick.color':'#4d575e','ytick.color':'#4d575e','svg.fonttype':'none','figure.facecolor':'#ffffff','axes.facecolor':'#ffffff'})
d=json.loads((ROOT/'evidence/historical-latency.json').read_text())
fig,ax=plt.subplots(figsize=(10.4,4.2),layout='constrained')
rows=d['turns']; x=np.arange(len(rows));produce=np.array([r['produce_s'] for r in rows]);density=np.array([r['density_s'] for r in rows]);other=np.array([r['total_s'] for r in rows])-produce-density
ax.bar(x,produce,color='#426e94',width=.57,label='Authoring / tools')
ax.bar(x,density,bottom=produce,color='#d38a45',width=.57,label='Density generation')
ax.bar(x,other,bottom=produce+density,color='#a5b0b9',width=.57,label='Repair + other work')
for i,r in enumerate(rows):ax.text(i,r['total_s']+2,f"{r['total_s']:.1f}",ha='center',fontsize=10)
ax.set_ylim(0,150);ax.set_xticks(x,[f'Turn {i+1}' for i in x]);ax.set_ylabel('Seconds until run_turn returns');ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.legend(loc='upper right',frameon=False,fontsize=10)
for ext in ['svg','png']:fig.savefig(DEST/f'latency.{ext}',dpi=180)
plt.close(fig)

d=json.loads((ROOT/'evidence/index-benchmark.json').read_text());rows=d['rows'];x=np.arange(len(rows));fig,ax=plt.subplots(figsize=(10.4,4.3),layout='constrained')
for offset,key,label,col in [(-.18,'baseline_ms','Original FactGraph','#426e94'),(.18,'indexed_ms','Indexed fact prototype','#26816c')]:
 vals=[r[key] for r in rows];ax.bar(x+offset,vals,width=.33,color=col,label=label)
 for i,v in enumerate(vals):ax.text(i+offset,v*1.11,f'{v:.1f}',ha='center',fontsize=10)
ax.set_yscale('log');ax.set_ylim(1,7000);ax.set_xticks(x,[f"{r['events']:,} events" for r in rows]);ax.set_ylabel('Projection time, ms (log scale)');ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True);ax.legend(loc='upper left',frameon=False)
for ext in ['svg','png']:fig.savefig(DEST/f'fact-index.{ext}',dpi=180)
plt.close(fig)
print('4 standalone chart artifacts generated.')
