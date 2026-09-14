"""Batch 4C/4D/4E：Orchestrator 请求状态 / Storage 单例与 cleanup / VectorStore。"""
from __future__ import annotations

import threading


class TestPptOrchestratorRequestState:
    def test_image_counters_reset_between_generations(self, tmp_path):
        from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator

        orch = PPTOrchestrator()
        orch.image_generation["attempted"] = 99
        orch.image_generation["generated"] = 7
        orch._generated_temp_images.append("/tmp/x.png")
        orch._reset_request_state()
        assert orch.image_generation["attempted"] == 0
        assert orch.image_generation["generated"] == 0
        assert orch._generated_temp_images == []

    def test_two_generations_do_not_leak_counters(self, tmp_path):
        from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator

        orch = PPTOrchestrator()
        out1 = tmp_path / "a.pptx"
        out2 = tmp_path / "b.pptx"
        r1 = orch.generate_from_theme("项目汇报", slide_count=4, output_path=str(out1))
        assert r1.success, r1.message
        # 人为污染再生成第二次
        orch.image_generation["generated"] = 5
        r2 = orch.generate_from_theme("工作总结", slide_count=4, output_path=str(out2))
        assert r2.success, r2.message
        # 第二次入口已 reset，不累加第一次/污染值
        assert orch.image_generation["generated"] <= orch.image_generation["attempted"]


class TestStorageSingleton:
    def test_get_storage_service_single_instance(self, monkeypatch):
        import office_agent.storage.storage_service as ss

        original = ss._storage_service
        ss._storage_service = None
        try:
            created = []
            real_cls = ss.StorageService

            def factory(*a, **k):
                inst = real_cls()
                created.append(inst)
                return inst

            monkeypatch.setattr(ss, "StorageService", factory)
            barrier = threading.Barrier(6)
            results = []

            def worker():
                barrier.wait()
                results.append(ss.get_storage_service())

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert all(r is results[0] for r in results)
            assert len(created) == 1
        finally:
            ss._storage_service = original


class TestVectorStoreAtomicUpdate:
    def test_update_chunks_atomic_visibility(self):
        from office_agent.knowledge_base.models import KnowledgeChunk
        from office_agent.knowledge_base.vector_store import VectorStore

        store = VectorStore()
        old = KnowledgeChunk(
            id="c1", document_id="d1", content="old content",
            chunk_index=0, metadata={},
        )
        store.add_chunks([old])

        seen_mid = []
        barrier = threading.Barrier(2)

        class SlowEmbedder:
            def embed_texts(self, texts):
                # 在 add 内部（锁内）会调用；用 barrier 只在第一次
                return [[0.1] * 8 for _ in texts]

            def embed_query(self, q):
                return [0.1] * 8

            def fit(self, texts):
                return None

            @property
            def dimension(self):
                return 8

        new = KnowledgeChunk(
            id="c1", document_id="d1", content="new content",
            chunk_index=0, metadata={},
        )

        def reader():
            barrier.wait()
            # 在 writer 持锁期间不应看到「既无旧也无新」的空洞
            # （update_chunks 同一把 RLock）
            with store._lock:
                ids = {c.chunk.id for c in store._chunks}
                seen_mid.append(ids)

        def writer():
            barrier.wait()
            store.update_chunks([new])

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # 任意观察点 c1 必须存在
        for ids in seen_mid:
            assert "c1" in ids
        # 最终是新内容
        final = [c for c in store._chunks if c.chunk.id == "c1"]
        assert final and final[0].chunk.content == "new content"

    def test_search_embed_not_under_lock(self, monkeypatch):
        """embed_query 调用时 _lock 不应被本线程持有。"""
        from office_agent.knowledge_base.models import KnowledgeChunk
        from office_agent.knowledge_base.vector_store import VectorStore

        class ProbeEmbedder:
            def __init__(self):
                self.held_during_embed = None

            def embed(self, texts):
                return [[1.0] * 4 for _ in texts]

            def embed_texts(self, texts):
                return self.embed(texts)

            def embed_query(self, q):
                lock = store._lock
                # 若本线程已持有 RLock，acquire 非阻塞会成功
                acquired = lock.acquire(blocking=False)
                if acquired:
                    lock.release()
                    self.held_during_embed = False
                else:
                    self.held_during_embed = True
                return [1.0] * 4

            def fit(self, texts):
                return None

            @property
            def dimension(self):
                return 4

        store = VectorStore(embedder=ProbeEmbedder())
        store.add_chunks([
            KnowledgeChunk(id="a", document_id="d", content="hello world",
                           chunk_index=0, metadata={}),
        ])
        store.search("hello")
        assert store.embedder.held_during_embed is False
