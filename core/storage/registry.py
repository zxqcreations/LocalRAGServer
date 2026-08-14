"""知识库/文档注册表（Repository 模式，SQLModel）。

MVP 用 SQLite；Phase 1+ 切换 PostgreSQL 只需改连接串。
chunk 正文唯一存放处，向量库 payload 只存索引键。

pyright 类型摩擦说明：SQLModel 不用 SQLAlchemy 2.0 的 Mapped 注解风格，
字段类属性（如 `Document.kb_id == x`）在 pyright 中解析为实例类型（str/datetime）
而非列表达式，导致 where/order_by 参数类型报错——运行时语义正确，
故本文件关闭 reportArgumentType（属 SQLModel + pyright 的已知组合限制）。
"""
# pyright: reportArgumentType=false
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

from core.ingest.chunker import Chunk
from core.ingest.state import Stage, assert_transition
from core.security.acl import hash_api_key, key_prefix, verify_api_key


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


class ApiKey(SQLModel, table=True):
    """API Key（架构 §8.1；设计 docs/design/acl-enforcement.md）。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    name: str
    key_prefix: str = Field(index=True)  # 前缀索引（8 字符，加速查找）
    key_hash: str  # scrypt 慢哈希 + 盐（明文绝不落库）
    kb_acl: str = Field(default='["*"]')  # json：["kb1",...] 或 ["*"]
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: datetime | None = None


class AdminUser(SQLModel, table=True):
    """管理端用户（web-admin-auth.md：admin/readonly 两档 RBAC）。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str  # Argon2id（人类口令专用档，审计 M-6）
    role: str = Field(default="admin")  # admin | readonly
    must_change_password: bool = True  # 首次登录强制改密
    created_at: datetime = Field(default_factory=_utcnow)


class AdminSession(SQLModel, table=True):
    """管理会话（session_id 哈希存储；闲置 30 分钟失效）。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True)
    session_hash: str = Field(index=True)  # 只存哈希（会话 Cookie 泄露不直接可用）
    expires_at: datetime
    created_at: datetime = Field(default_factory=_utcnow)


class Annotation(SQLModel, table=True):
    """人工标注（审计 F17：调试台标注沉淀为评测集；幂等键 (kb_id, query)）。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    kb_id: str = Field(index=True)
    query: str
    doc_id: str = ""
    chunk_id: str = ""
    is_helpful: bool = True
    created_by: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class AuditLog(SQLModel, table=True):
    """审计日志（acl-enforcement.md §5 / 审计 F-05）：只追加不修改。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    actor: str = ""  # key_id 或 "master"
    action: str = ""  # search | ingest | delete | key_manage
    kb_id: str = ""
    ip: str = ""
    trace_id: str = ""
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

    # ---------- API Key（ACL 强制点，审计 F-13/F-02） ----------

    def create_api_key(
        self,
        name: str,
        kb_acl: list[str] | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKey, str]:
        """签发 Key：返回 (记录, 明文)；明文仅此一次，DB 只存 scrypt 哈希。"""
        import secrets as _secrets

        raw = _secrets.token_urlsafe(32)
        # 审计 M-5：强制 UTC-aware 规范化（naive 输入按 UTC 解释，拒绝歧义）
        normalized_expiry = expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                normalized_expiry = expires_at.replace(tzinfo=UTC)
            else:
                normalized_expiry = expires_at.astimezone(UTC)
        record = ApiKey(
            name=name,
            key_prefix=key_prefix(raw),
            key_hash=hash_api_key(raw),
            kb_acl=json.dumps(kb_acl or ["*"], ensure_ascii=False),
            expires_at=normalized_expiry,
        )
        with Session(self._engine) as s:
            s.add(record)
            s.commit()
            s.refresh(record)
        return record, raw

    def verify_api_key(self, raw: str) -> ApiKey | None:
        """前缀查找 + scrypt 验证 + 过期检查；通过则返回记录并刷新 last_used_at。"""
        prefix = key_prefix(raw)
        with Session(self._engine) as s:
            candidates = s.exec(
                select(ApiKey).where(ApiKey.key_prefix == prefix)  # type: ignore[arg-type]
            ).all()
            for record in candidates:
                if not verify_api_key(raw, record.key_hash):
                    continue
                # SQLite 存取丢失时区信息：统一用 naive UTC 比较
                now = _utcnow().replace(tzinfo=None)
                if record.expires_at is not None and record.expires_at <= now:
                    return None
                record.last_used_at = now
                s.add(record)
                s.commit()
                s.refresh(record)  # 防止会话关闭后属性过期（DetachedInstanceError）
                return record
        return None

    def revoke_api_key(self, key_id: str) -> None:
        with Session(self._engine) as s:
            s.exec(delete(ApiKey).where(ApiKey.id == key_id))  # type: ignore[arg-type]
            s.commit()

    def list_api_keys(self) -> list[ApiKey]:
        with Session(self._engine) as s:
            return list(s.exec(select(ApiKey).order_by(ApiKey.created_at)))

    def get_api_key(self, key_id: str) -> ApiKey | None:
        with Session(self._engine) as s:
            return s.get(ApiKey, key_id)

    # ---------- 标注（审计 F17：幂等覆盖，不追加） ----------

    def upsert_annotation(
        self,
        kb_id: str,
        query: str,
        doc_id: str,
        chunk_id: str,
        is_helpful: bool,
        created_by: str,
    ) -> Annotation:
        with Session(self._engine) as s:
            existing = s.exec(
                select(Annotation).where(
                    Annotation.kb_id == kb_id, Annotation.query == query
                )
            ).first()
            if existing is not None:
                existing.doc_id = doc_id
                existing.chunk_id = chunk_id
                existing.is_helpful = is_helpful
                existing.created_by = created_by
                s.add(existing)
                s.commit()
                s.refresh(existing)
                return existing
            entry = Annotation(
                kb_id=kb_id,
                query=query,
                doc_id=doc_id,
                chunk_id=chunk_id,
                is_helpful=is_helpful,
                created_by=created_by,
            )
            s.add(entry)
            s.commit()
            s.refresh(entry)
            return entry

    def list_annotations(self, kb_id: str, limit: int = 200) -> list[Annotation]:
        with Session(self._engine) as s:
            return list(
                s.exec(
                    select(Annotation)
                    .where(Annotation.kb_id == kb_id)
                    .order_by(Annotation.created_at.desc())  # type: ignore[attr-defined]
                    .limit(limit)
                )
            )

    # ---------- 审计（F-05：只追加） ----------

    def record_audit(
        self,
        actor: str,
        action: str,
        kb_id: str = "",
        ip: str = "",
        trace_id: str = "",
    ) -> None:
        entry = AuditLog(
            actor=actor, action=action, kb_id=kb_id, ip=ip, trace_id=trace_id
        )
        with Session(self._engine) as s:
            s.add(entry)
            s.commit()

    # ---------- 管理端（web-admin-auth.md） ----------

    def ensure_admin_user(
        self, username: str, password_hash: str, role: str = "admin"
    ) -> AdminUser:
        """幂等创建管理用户（已存在则返回既有记录）。"""
        with Session(self._engine) as s:
            existing = s.exec(
                select(AdminUser).where(AdminUser.username == username)  # type: ignore[arg-type]
            ).first()
            if existing is not None:
                return existing
            user = AdminUser(username=username, password_hash=password_hash, role=role)
            s.add(user)
            s.commit()
            s.refresh(user)
            return user

    def get_admin_user_by_id(self, user_id: str) -> AdminUser | None:
        with Session(self._engine) as s:
            return s.get(AdminUser, user_id)

    def get_admin_user(self, username: str) -> AdminUser | None:
        with Session(self._engine) as s:
            return s.exec(
                select(AdminUser).where(AdminUser.username == username)  # type: ignore[arg-type]
            ).first()

    def set_admin_password(self, user_id: str, password_hash: str) -> None:
        with Session(self._engine) as s:
            user = s.get(AdminUser, user_id)
            if user is None:
                raise LookupError(f"管理用户不存在：{user_id}")
            user.password_hash = password_hash
            user.must_change_password = False
            s.add(user)
            s.commit()

    def set_admin_initial_password(self, user_id: str, password_hash: str) -> None:
        """测试/引导用：重置密码但保留强制改密标记（契约：初始密码必须走改密流程）。"""
        with Session(self._engine) as s:
            user = s.get(AdminUser, user_id)
            if user is None:
                raise LookupError(f"管理用户不存在：{user_id}")
            user.password_hash = password_hash
            user.must_change_password = True
            s.add(user)
            s.commit()

    def create_admin_session(self, user_id: str, session_hash: str, expires_at: datetime) -> None:
        with Session(self._engine) as s:
            s.add(
                AdminSession(user_id=user_id, session_hash=session_hash, expires_at=expires_at)
            )
            s.commit()

    def find_admin_session(self, session_hash: str) -> AdminSession | None:
        with Session(self._engine) as s:
            return s.exec(
                select(AdminSession).where(AdminSession.session_hash == session_hash)  # type: ignore[arg-type]
            ).first()

    def revoke_admin_session(self, session_hash: str) -> None:
        with Session(self._engine) as s:
            s.exec(delete(AdminSession).where(AdminSession.session_hash == session_hash))  # type: ignore[arg-type]
            s.commit()

    def list_audit(self, limit: int = 50) -> list[AuditLog]:
        with Session(self._engine) as s:
            return list(
                s.exec(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))  # type: ignore[arg-type]
            )

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
