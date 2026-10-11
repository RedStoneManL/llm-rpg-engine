diff --git a/PRODUCT_EXECUTION.md b/PRODUCT_EXECUTION.md
index 7c92232..d29839c 100644
--- a/PRODUCT_EXECUTION.md
+++ b/PRODUCT_EXECUTION.md
@@ -38,6 +38,7 @@ Library 文档记录的是另一份后续代码快照，不能替代这里的源
 | M2-01 | P1 | 已验收（离线宿主前置层） | 仅可信 Python 宿主的私有记忆读取 handle | world/store/session/role identity 绑定、关闭撤销和跨 session/store 反例通过；默认 app/tools/DM 未接入，非生产认证 |
 | M3-01 | P1 | 已验收（新公开历史 API） | 完整历史的私有记忆过滤 DTO | `Engine.public_history()` 过滤 active/retracted 私有行及显式引用；既有 CLI/GUI/GM/raw-store 未接入 |
 | M3b-01 | P1 | 已验收（续玩输出隔离） | resume recap 不读取 opt-in 私有记忆 slice | real store→projection→`_print_resume_recap` 覆盖 active/retracted、当前/其他角色私有记录；其余出口未测 |
+| M4-01 | P1 | 已验收（离线宿主原子写入） | batch/source/output 绑定的私有解释写入 | 红例驱动修复后，stage/publish 双重绑定复验、savepoint rollback、篡改/跨生命周期拒绝通过；默认 app 禁用 |
 
 ## 执行规则
 
diff --git a/engine/store.py b/engine/store.py
index 37a7ff7..1549686 100644
--- a/engine/store.py
+++ b/engine/store.py
@@ -254,6 +254,7 @@ class EventBatch:
         self.db_path, self.jsonl_path = store.db_path, store.jsonl_path
         self.turn = turn if turn is not None else max((e.get('turn') or 0 for e in self._snapshot if not e.get('retracted')), default=0) + 1
         self.preflight = preflight or store.preflight
+        self.publish_validators = []
         self._last_seq = max((e.get('seq') or 0 for e in self._snapshot), default=0)
 
     def next_turn(self):
@@ -292,6 +293,9 @@ class EventBatch:
                 'last_seq': self._last_seq + len(self.events) if len(self.events)>start else 0,
                 'revision': self.revision, 'duplicate': False, 'mirror_pending': False}
 
+    def add_publish_validator(self, validator):
+        self.publish_validators.append(validator)
+
     def iter_events(self, include_retracted=False):
         for ev in self._snapshot + self.events:
             if include_retracted or not ev.get('retracted'):
@@ -301,6 +305,7 @@ class EventBatch:
     def savepoint(self):
         start = len(self.events)
         retractions_start = len(self.retractions)
+        validators_start = len(self.publish_validators)
         # A hook may stage a rewind as well as append events.  Both are part of
         # its proposal: if the hook fails, leaving the retraction intent behind
         # would make a later publish retract already-committed history even
@@ -315,12 +320,21 @@ class EventBatch:
         except BaseException:
             del self.events[start:]
             del self.retractions[retractions_start:]
+            del self.publish_validators[validators_start:]
             for ev, was_retracted in prior_retracted:
                 ev['retracted'] = was_retracted
             raise
 
     def publish(self, *, action_id=None):
+        for validator in self.publish_validators:
+            validator()
+        original_preflight = self.preflight
+        def final_preflight(history):
+            if original_preflight:
+                original_preflight(history)
+            for validator in self.publish_validators:
+                validator()
         events = [{k:v for k,v in ev.items() if k != 'seq'} for ev in self.events]
         return self.store.append_many(events, expected_revision=self.revision,
-                                      action_id=action_id, preflight=self.preflight,
+                                      action_id=action_id, preflight=final_preflight,
                                       retractions=self.retractions)
diff --git a/systems/interpretive_memory.py b/systems/interpretive_memory.py
index 6f6aa3e..acf5a54 100644
--- a/systems/interpretive_memory.py
+++ b/systems/interpretive_memory.py
@@ -6,6 +6,8 @@ application registry does not register it.  Reading is actor-scoped through
 ``records_for_actor`` and never uses ``inject`` or ``recall``.
 """
 from __future__ import annotations
+import hashlib
+import json
 
 from kernel.contextsystem import ContextSystem
 from engine.log import get_logger
@@ -67,6 +69,58 @@ def open_memory_read_session(world: dict, store, role: str) -> MemoryReadSession
     return MemoryReadSession(world, store, role)
 
 
+class MemoryWriteSession:
+    """Trusted-host staging adapter bound to one foreground EventBatch."""
+    def __init__(self, batch, registry, role: str, source_event: dict):
+        self.batch, self.registry, self.role = batch, registry, role
+        self.registry_identity = registry
+        self.store, self.revision, self.turn = batch.store, batch.revision, batch.turn
+        self.source_id = source_event.get("id")
+        self.source_digest = _event_digest(source_event)
+
+    def _verify_binding(self):
+        if (self.batch.store is not self.store or self.registry is not self.registry_identity
+                or self.batch.revision != self.revision or self.batch.turn != self.turn):
+            raise PermissionError("memory write session no longer matches batch")
+
+    def stage(self, text: str, memory_id: str):
+        self._verify_binding()
+        source = next((e for e in self.batch.events if e.get("id") == self.source_id), None)
+        if (source is None or source.get("retracted") or source.get("type") == "character_interpretation_recorded"
+                or _event_digest(source) != self.source_digest):
+            raise PermissionError("memory source is missing, retracted, or changed")
+        if self.registry.owner_of_event("character_interpretation_recorded") is None:
+            raise PermissionError("interpretive memory is not enabled for this registry")
+        def verify_before_publish():
+            self._verify_binding()
+            source_now = next((e for e in self.batch.events if e.get("id") == self.source_id), None)
+            if source_now is None or source_now.get("retracted") or _event_digest(source_now) != self.source_digest:
+                raise PermissionError("memory source changed before publish")
+        from kernel.events import kernel_event
+        record = kernel_event("character_interpretation_recorded", day=source["day"], scene=source["scene"],
+            summary="private character interpretation", deltas={"actor":self.role, "text":text,
+            "memory_id":memory_id, "source_event_id":self.source_id}, turn=self.turn)
+        self.batch.append(record)
+        record = self.batch.events[-1]
+        record_digest = _event_digest(record)
+        def verify_record():
+            if record not in self.batch.events or _event_digest(record) != record_digest:
+                raise PermissionError("memory record changed before publish")
+        self.batch.add_publish_validator(verify_before_publish)
+        self.batch.add_publish_validator(verify_record)
+
+
+def _event_digest(event):
+    return hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
+
+
+def open_memory_write_session(batch, registry, role: str, source_event: dict) -> MemoryWriteSession:
+    """Trusted host factory; source must already be staged in this batch."""
+    if not isinstance(role, str) or not role or source_event not in batch.events:
+        raise PermissionError("host must bind a current staged source and role")
+    return MemoryWriteSession(batch, registry, role, source_event)
+
+
 class InterpretiveMemorySystem(ContextSystem):
     """Private, replayable interpretations with no commit section."""
 
diff --git a/tests/systems/test_interpretive_memory.py b/tests/systems/test_interpretive_memory.py
index f98cb29..42d3d47 100644
--- a/tests/systems/test_interpretive_memory.py
+++ b/tests/systems/test_interpretive_memory.py
@@ -1,12 +1,15 @@
+import asyncio
 import pytest
 
 from app.engine import build_engine
 from engine.store import EventStore
+from engine.store import EventBatch
 from kernel.events import kernel_event
 from kernel.projection import project
 from kernel.registry import Registry
 from systems.interpretive_memory import (InterpretiveMemorySystem, records_for_actor,
                                          open_memory_read_session)
+from systems.interpretive_memory import open_memory_write_session
 
 
 def _registry():
@@ -88,3 +91,104 @@ def test_default_model_tool_registry_cannot_issue_or_read_memory_handles(tmp_pat
         assert not any("memory" in name for name in tools._tools)
     finally:
         engine.store.close()
+
+
+def test_host_write_session_requires_unchanged_current_batch_source(tmp_path):
+    registry = _registry()
+    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl",
+                    {"fact_asserted", "character_interpretation_recorded"}) as store:
+        batch = EventBatch(store)
+        source = kernel_event("fact_asserted", day=1, scene="inn", summary="source",
+            deltas={"subject":"hero", "predicate":"mood", "value":"calm"}, turn=1)
+        batch.append(source)
+        source = batch.events[0]
+        writer = open_memory_write_session(batch, registry, "hero", source)
+        writer.stage("host interpretation", "m1")
+        assert [e["type"] for e in batch.events] == ["fact_asserted", "character_interpretation_recorded"]
+        batch.publish(action_id="memory-write")
+        assert len(list(store.iter_events())) == 2
+
+
+def test_host_write_session_rejects_changed_or_cross_batch_source(tmp_path):
+    registry = _registry()
+    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl",
+                    {"fact_asserted", "character_interpretation_recorded"}) as store:
+        batch = EventBatch(store)
+        source = kernel_event("fact_asserted", day=1, scene="inn", summary="source",
+            deltas={"subject":"hero", "predicate":"mood", "value":"calm"}, turn=1)
+        batch.append(source); source = batch.events[0]
+        writer = open_memory_write_session(batch, registry, "hero", source)
+        source["summary"] = "tampered"
+        with pytest.raises(PermissionError): writer.stage("x", "m1")
+        assert len(batch.events) == 1
+        with pytest.raises(PermissionError):
+            open_memory_write_session(EventBatch(store), registry, "hero", source)
+
+
+def test_memory_publish_rechecks_source_and_keeps_store_unchanged(tmp_path):
+    registry = _registry()
+    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
+        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
+        batch.append(source); source = batch.events[0]
+        open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
+        source["summary"] = "tampered"
+        with pytest.raises(PermissionError): batch.publish(action_id="bad")
+        assert list(store.iter_events()) == [] and store.revision == 0
+
+
+@pytest.mark.parametrize("exc", [RuntimeError, asyncio.CancelledError])
+def test_memory_stage_savepoint_rollback_removes_validator(tmp_path, exc):
+    registry = _registry()
+    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
+        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
+        batch.append(source); source = batch.events[0]
+        with pytest.raises(exc):
+            with batch.savepoint():
+                open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
+                raise exc()
+        assert len(batch.events) == 1 and batch.publish_validators == []
+        batch.publish(action_id="ordinary")
+        assert len(list(store.iter_events())) == 1
+
+
+def test_preflight_mutation_cannot_bypass_memory_final_validation(tmp_path):
+    registry = _registry()
+    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
+        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
+        batch.append(source); source = batch.events[0]
+        open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
+        def mutate(history): source["summary"] = "changed-by-preflight"
+        batch.preflight = mutate
+        with pytest.raises(PermissionError): batch.publish(action_id="blocked")
+        assert list(store.iter_events()) == [] and store.revision == 0
+
+
+def test_original_preflight_error_is_not_masked_by_memory_validator(tmp_path):
+    registry = _registry()
+    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
+        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
+        batch.append(source); source = batch.events[0]
+        open_memory_write_session(batch, registry, "hero", source).stage("private", "m1")
+        batch.preflight = lambda history: (_ for _ in ()).throw(RuntimeError("original preflight"))
+        with pytest.raises(RuntimeError, match="original preflight"): batch.publish(action_id="blocked")
+        assert list(store.iter_events()) == [] and store.revision == 0
+
+
+@pytest.mark.parametrize("mutation", ["text", "actor", "source", "remove_source", "retract_source", "turn", "revision", "registry", "store"])
+def test_memory_write_lifecycle_mutations_are_rejected(tmp_path, mutation):
+    registry = _registry()
+    with EventStore(tmp_path / "events.db", tmp_path / "events.jsonl", {"fact_asserted", "character_interpretation_recorded"}) as store:
+        batch = EventBatch(store); source = kernel_event("fact_asserted", day=1, scene="s", summary="source", deltas={"subject":"hero","predicate":"mood","value":"ok"}, turn=1)
+        batch.append(source); source = batch.events[0]; writer = open_memory_write_session(batch, registry, "hero", source); writer.stage("private", "m1")
+        record = batch.events[-1]
+        if mutation == "text": record["deltas"]["text"] = "changed"
+        elif mutation == "actor": record["deltas"]["actor"] = "npc"
+        elif mutation == "source": record["deltas"]["source_event_id"] = "other"
+        elif mutation == "remove_source": batch.events.remove(source)
+        elif mutation == "retract_source": source["retracted"] = True
+        elif mutation == "turn": batch.turn += 1
+        elif mutation == "revision": batch.revision += 1
+        elif mutation == "registry": writer.registry = Registry()
+        elif mutation == "store": batch.store = object()
+        with pytest.raises(PermissionError): batch.publish(action_id="blocked")
+        assert list(store.iter_events()) == [] and store.revision == 0
