"""知识库/文档注册表（Repository 模式，SQLModel）。

MVP 用 SQLite；Phase 1+ 切换 PostgreSQL 只需改连接串。
chunk 正文唯一存放处，向量库 payload 只存索引键。

pyright 类型摩擦说明：SQLModel 不用 SQLAlchemy 2.0 的 Mapped 注解风格，
字段类属性（如 `Document.kb_id == x`）在 pyright 中解析为实例类型（str/datetime）
而非列表达式，导致 where/order_by 参数类型报错——运行时语义正确，
故本文件关闭 reportArgumentType（属 SQLModel + pyright 的已知组合限制）。
"""
# pyright: reportArgumentType=false
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

from core.ingest.chunker import Chunk
from core.ingest.state import Stage, assert_transition


def _utcnow() -> datetime:
    return datetime.now(UTC)


class KnowledgeBase(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    name: str
    kb_type: str = Field(default="document")  # document | code | web
    created_at: datetime = Field(default_factory=_utcnow)


class Document(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    kb_id: str = Field(index=True)
    title: str
    source: str
    content_hash: str
    # 状态机域（契约 §3）：uploaded|parsed|chunked|embedded|indexed|ready|failed
    status: str = Field(default=Stage.UPLOADED.value)
    chunk_count: int = 0
    error: str | None = None
    # 解析管线版本戳（审计 ARC-003：parser+chunker+embedder 三元组；策略变更后据此重索引）
    pipeline_version: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class IngestJob(SQLModel, table=True):
    """摄取任务（契约 §3：stage/attempt/error 持久化，进程崩溃可恢复）。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    doc_id: str = Field(index=True)
    kb_id: str = Field(index=True)
    stage: str = Field(default=Stage.UPLOADED.value, index=True)
    attempt: int = 0
    failed_at: str | None = None  # 失败发生阶段（恢复转移定位，契约实现细节）
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ChunkRow(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    kb_id: str = Field(index=True)
    doc_id: str = Field(index=True)
    index: int
    content: str
    created_at: datetime = Field(default_factory=_utcnow)


class Registry:
    def __init__(self, database_url: str) -> None:
        # SQLite 用 NullPool：每次操作独立连接，避免测试/短生命周期进程中连接滞留；
        # PG 保持默认连接池（生产路径）
        kwargs = {"poolclass": NullPool} if database_url.startswith("sqlite") else {}
        self._engine = create_engine(database_url, **kwargs)
        SQLModel.metadata.create_all(self._engine)

    def session(self) -> Iterator[Session]:
        with Session(self._engine) as s:
            yield s

    def close(self) -> None:
        """释放连接池（应用退出前调用）。"""
        self._engine.dispose()

    # ---------- 知识库 ----------

    def create_kb(self, name: str, kb_type: str = "document") -> KnowledgeBase:
        kb = KnowledgeBase(name=name, kb_type=kb_type)
        with Session(self._engine) as s:
            s.add(kb)
            s.commit()
            s.refresh(kb)
        return kb

    def get_kb(self, kb_id: str) -> KnowledgeBase | None:
        with Session(self._engine) as s:
            return s.get(KnowledgeBase, kb_id)

    def list_kbs(self) -> list[KnowledgeBase]:
        with Session(self._engine) as s:
            # SQLModel 字段类属性在 pyright 中解析为实例类型而非列（typing 摩擦，运行时正确）
            return list(  # type: ignore[arg-type]
                s.exec(select(KnowledgeBase).order_by(KnowledgeBase.created_at))
            )

    # ---------- 文档 ----------

    def create_document(
        self, kb_id: str, title: str, source: str, content_hash: str
    ) -> Document:
        doc = Document(kb_id=kb_id, title=title, source=source, content_hash=content_hash)
        with Session(self._engine) as s:
            s.add(doc)
            s.commit()
            s.refresh(doc)
        return doc

    def get_document(self, kb_id: str, doc_id: str) -> Document | None:
        with Session(self._engine) as s:
            doc = s.get(Document, doc_id)
        if doc is not None and doc.kb_id != kb_id:
            return None
        return doc

    def find_document_by_hash(self, kb_id: str, content_hash: str) -> Document | None:
        """按内容哈希查重（幂等摄取：同内容文档只建一次）。"""
        with Session(self._engine) as s:
            return s.exec(
                select(Document).where(
                    Document.kb_id == kb_id, Document.content_hash == content_hash
                )
            ).first()

    def list_documents(self, kb_id: str) -> list[Document]:
        with Session(self._engine) as s:
            return list(s.exec(select(Document).where(Document.kb_id == kb_id)))

    def get_documents_by_ids(self, doc_ids: list[str]) -> dict[str, Document]:
        with Session(self._engine) as s:
            rows = s.exec(select(Document).where(Document.id.in_(doc_ids))).all()  # type: ignore[attr-defined]
        return {r.id: r for r in rows}

    def set_pipeline_version(self, doc_id: str, version: str) -> None:
        with Session(self._engine) as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                raise LookupError(f"文档不存在：{doc_id}")
            doc.pipeline_version = version
            s.add(doc)
            s.commit()

    def mark_document_failed(self, doc_id: str, error: str) -> None:
        with Session(self._engine) as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                raise LookupError(f"文档不存在：{doc_id}")
            doc.status = "failed"
            doc.error = error
            s.add(doc)
            s.commit()

    def set_chunks(self, doc_id: str, kb_id: str, chunks: list[Chunk]) -> list[str]:
        """替换该文档的全部 chunk（幂等重摄入），返回 chunk id 列表。"""
        rows = [
            ChunkRow(
                id=uuid.uuid4().hex,
                kb_id=kb_id,
                doc_id=doc_id,
                index=c.index,
                content=c.text,
            )
            for c in chunks
        ]
        with Session(self._engine) as s:
            s.exec(delete(ChunkRow).where(ChunkRow.doc_id == doc_id))  # type: ignore[arg-type]
            doc = s.get(Document, doc_id)
            if doc is None:
                raise LookupError(f"文档不存在：{doc_id}")
            s.add_all(rows)
            chunk_ids = [r.id for r in rows]  # commit 前取值，避免会话关闭后属性过期
            doc.chunk_count = len(rows)
            doc.status = "ready"
            doc.error = None
            s.add(doc)
            s.commit()
        return chunk_ids

    def get_chunk_contents(self, chunk_ids: list[str]) -> dict[str, str]:
        with Session(self._engine) as s:
            rows = s.exec(select(ChunkRow).where(ChunkRow.id.in_(chunk_ids))).all()  # type: ignore[attr-defined]
        return {r.id: r.content for r in rows}

    def get_chunk_doc_map(self, chunk_ids: list[str]) -> dict[str, str]:
        """chunk_id → doc_id 归属映射。"""
        with Session(self._engine) as s:
            rows = s.exec(select(ChunkRow).where(ChunkRow.id.in_(chunk_ids))).all()  # type: ignore[attr-defined]
        return {r.id: r.doc_id for r in rows}

    def list_chunks(self, kb_id: str) -> list[tuple[str, str]]:
        """KB 全量 chunk（id, content），按文档与序号排序——供混合检索的 BM25 建索引。"""
        with Session(self._engine) as s:
            rows = s.exec(
                select(ChunkRow).where(ChunkRow.kb_id == kb_id).order_by(  # type: ignore[arg-type]
                    ChunkRow.doc_id, ChunkRow.index
                )
            ).all()
        return [(r.id, r.content) for r in rows]

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        with Session(self._engine) as s:
            s.exec(delete(ChunkRow).where(ChunkRow.doc_id == doc_id))  # type: ignore[arg-type]
            s.exec(  # type: ignore[arg-type]
                delete(Document).where(  # type: ignore[arg-type]
                    Document.id == doc_id, Document.kb_id == kb_id
                )
            )
            s.commit()

    def count_documents(self, kb_id: str) -> int:
        with Session(self._engine) as s:
            return len(s.exec(select(Document).where(Document.kb_id == kb_id)).all())

    # ---------- 摄取任务（状态机） ----------

    def create_job(self, doc_id: str, kb_id: str) -> IngestJob:
        job = IngestJob(doc_id=doc_id, kb_id=kb_id)
        with Session(self._engine) as s:
            s.add(job)
            s.commit()
            s.refresh(job)
        return job

    def get_job(self, job_id: str) -> IngestJob | None:
        with Session(self._engine) as s:
            return s.get(IngestJob, job_id)

    def transition_job(self, job_id: str, to: Stage, error: str | None = None) -> None:
        """状态转移（契约 §2.1：非法转移拒绝；§2.2：阶段幂等由调用方先查状态）。

        同步更新 document.status；failed 转移记录 attempt 与 error。
        """
        with Session(self._engine) as s:
            job = s.get(IngestJob, job_id)
            if job is None:
                raise LookupError(f"任务不存在：{job_id}")
            frm = Stage(job.stage)
            if frm == to:  # 契约 v1.1：同状态转移为幂等 no-op（恢复后阶段方法重入）
                return
            assert_transition(frm, to)
            job.stage = to.value
            job.updated_at = _utcnow()
            if to == Stage.FAILED:
                job.attempt += 1
                job.failed_at = frm.value
                job.error = error
            elif frm == Stage.FAILED:
                job.error = None  # 恢复后清错误
            doc = s.get(Document, job.doc_id)
            if doc is not None:
                doc.status = to.value
                if to == Stage.FAILED:
                    doc.error = error
                elif frm == Stage.FAILED:
                    doc.error = None
                s.add(doc)
            s.add(job)
            s.commit()
