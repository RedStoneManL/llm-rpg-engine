# Code Graph(模块依赖图索引)

由 [`pydeps`](https://github.com/thebjorn/pydeps) 静态分析 `engine/` 生成的内部依赖图。

## 产物

- **`engine_deps.svg` / `.dot`** — 模块依赖图(pydeps)。16 模块,38 条内部依赖边。
- **`engine_calls.svg` / `.dot`** — 函数级调用图(pyan3,~398 边,启发式,含少量噪声)。看"谁调谁"到函数粒度。
- **`tags`** — universal-ctags 符号索引(165 符号),编辑器跳转定义用。

## 怎么读

**箭头方向 = pydeps 约定:`A -> B` 表示「A 被 B import」(即 B 依赖 A)。**
颜色按"被依赖热度"(bacon 数)着色:越红 = 被越多模块依赖。

由此一眼可见的事实:
- `log`(最红)被**所有**模块依赖(共享日志基础)。
- `store`(真相源)被 `cli/compact/recall/rewind` 依赖。
- `schema` 仅被 `store`(+cli)依赖。
- `cli` 是汇聚点(几乎所有模块的箭头都指向它)——它是唯一聚合全部 engine 的枢纽。
- `recall` 聚合 `archive+embed+store+vectorstore`;`rewind` 聚合 `store+archive+compact+recall`。
- **无环**(分层架构:Tier0 log/schema → … → Tier4 cli)。

> `bin/rpg` 依赖 `engine.cli`;`hooks/pre_llm_call` 依赖 `engine.compact`(经 sys.path)。这两个入口未纳入上图(图只析 `engine/` 包内)。完整交互关系见上层 `../../README.md` 的"模块依赖图 / 模块逐个深入"。

## 重新生成

依赖:`pipx install pydeps` + 系统 `graphviz`(`dot`)。已在 `requirements-dev.txt` 标注。

```bash
# 在仓库根执行(gen.sh 封装了同样的命令)
bash docs/codegraph/gen.sh
```

`gen.sh` 把 skill `.venv` 的 site-packages 加进 `PYTHONPATH`,好让 pydeps 解析到 `engine` 的外部依赖(numpy/fastembed),`--only engine` 把图限定在内部边。
