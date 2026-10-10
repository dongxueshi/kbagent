import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import pymysql
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from minio.error import S3Error
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse
from urllib3.exceptions import HTTPError

from .agent import answer_events, event_error
from .config import Settings
from .importer import ImportFailure, extract_text
from .object_storage import ObjectStorage
from .repositories import RepositoryFailure, RepositoryService
from .store import Store

STATIC = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


class SpaceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v):
        if not v.strip():
            raise ValueError("名称不能为空")
        return v.strip()


class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)
    aliases: list[str] | None = Field(default=None, max_length=50)

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, values):
        if values is None:
            return None
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("别名需为 1 至 200 字符")
        return list(dict.fromkeys(value.strip() for value in values))

    @field_validator("title", "content")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("标题和内容不能为空")
        return v.strip()


class QuestionInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("问题不能为空")
        return v.strip()


class RepositoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(pattern="^(gitlab|local)$")
    source: str = Field(min_length=1, max_length=1024)
    ref: str = Field(default="", max_length=200)

    @field_validator("name", "source", "ref")
    @classmethod
    def stripped(cls, value):
        return value.strip()


class LinkBinding(BaseModel):
    target_id: str = Field(min_length=1, max_length=64)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings)
    objects = ObjectStorage(settings)
    repositories = RepositoryService(store, objects, settings)
    active_conversations: set[str] = set()
    agent_slots = asyncio.Semaphore(4)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.initialize)
        await asyncio.to_thread(objects.initialize)

        async def cleanup():
            while True:
                try:
                    await asyncio.to_thread(objects.collect, store)
                except Exception as exc:
                    logger.warning("Storage cleanup deferred (%s)", type(exc).__name__)
                await asyncio.sleep(60)

        await asyncio.to_thread(store.recover_repository_jobs)
        repository_worker = asyncio.create_task(repositories.worker())
        worker = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            repositories.stopping = True
            # Drain the active Git job rather than leaving a thread writing after shutdown.
            await repository_worker
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="KB Agent", version="0.1.0", lifespan=lifespan)
    app.state.objects = objects
    app.state.store = store
    app.state.settings = settings
    app.state.repositories = repositories
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    async def storage_error(request, exc):
        logger.warning("Storage request failed (%s)", type(exc).__name__)
        return JSONResponse(
            status_code=503, content={"detail": "存储服务暂时不可用，请检查 MySQL 和 MinIO 配置"}
        )

    for error in (pymysql.MySQLError, S3Error, HTTPError):
        app.add_exception_handler(error, storage_error)

    def require_space(space_id):
        if not store.space(space_id):
            raise HTTPException(404, "知识库不存在")

    def require_document(space_id, document_id):
        document = store.document(space_id, document_id)
        if not document:
            raise HTTPException(404, "文档不存在")
        return document

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        with store.connection() as db:
            db.execute("SELECT count(*) FROM spaces").fetchone()
        if not objects.health():
            raise HTTPException(503, "MinIO 存储桶不可用")
        return {"status": "ok", "database": "ok", "object_storage": "ok"}

    @app.get("/api/settings")
    def status():
        return {
            "mode": "agent" if settings.agent_enabled else "retrieval",
            "model": settings.model if settings.agent_enabled else None,
            "sdk": "deepagents",
            "database": "mysql",
            "object_storage": "minio",
            "max_upload_bytes": settings.max_upload_bytes,
        }

    @app.get("/api/spaces")
    def list_spaces():
        return store.spaces()

    @app.post("/api/spaces", status_code=201)
    def new_space(body: SpaceInput):
        return store.create_space(body.name, body.description)

    @app.post("/api/demo", status_code=201)
    def seed_demo():
        return store.seed_demo()

    @app.delete("/api/spaces/{space_id}", status_code=204)
    def delete_space(space_id: str):
        require_space(space_id)
        if active_conversations:
            raise HTTPException(409, "问答正在运行，请完成后再删除知识库")
        if any(
            r["sync_status"] in {"queued", "running"} or r["wiki_status"] in {"queued", "running"}
            for r in store.repositories(space_id)
        ):
            raise HTTPException(409, "请等待代码仓库任务完成后再删除知识库")
        store.delete_space(space_id)
        objects.collect(store)

    @app.get("/api/spaces/{space_id}/documents")
    def list_documents(space_id: str):
        require_space(space_id)
        return store.documents(space_id)

    @app.post("/api/spaces/{space_id}/documents", status_code=201)
    def new_document(space_id: str, body: DocumentInput):
        require_space(space_id)
        return store.save_document(space_id, body.title, body.content, aliases=body.aliases)

    @app.get("/api/spaces/{space_id}/documents/{document_id}")
    def get_document(space_id: str, document_id: str):
        return require_document(space_id, document_id)

    @app.put("/api/spaces/{space_id}/documents/{document_id}")
    def update_document(space_id: str, document_id: str, body: DocumentInput):
        if require_document(space_id, document_id).get("repository"):
            raise HTTPException(409, "仓库文档由同步管理，请修改代码并重新同步")
        return store.save_document(
            space_id, body.title, body.content, document_id=document_id, aliases=body.aliases
        )

    @app.get("/api/spaces/{space_id}/documents/{document_id}/relations")
    def relations(space_id: str, document_id: str):
        result = store.relations(space_id, document_id)
        if result is None:
            raise HTTPException(404, "文档不存在")
        return result

    @app.get("/api/spaces/{space_id}/documents/{document_id}/graph")
    def graph(space_id: str, document_id: str, depth: int = Query(default=1, ge=1, le=2)):
        result = store.graph(space_id, document_id, depth)
        if result is None:
            raise HTTPException(404, "文档不存在")
        return result

    @app.put("/api/spaces/{space_id}/documents/{document_id}/links/{link_id}")
    def bind_link(space_id: str, document_id: str, link_id: int, body: LinkBinding):
        if require_document(space_id, document_id).get("repository"):
            raise HTTPException(409, "仓库 Wiki 链接由生成任务管理")
        try:
            store.bind_link(space_id, document_id, link_id, body.target_id)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        return {"status": "resolved", "target_id": body.target_id}

    @app.delete("/api/spaces/{space_id}/documents/{document_id}", status_code=204)
    def delete_document(space_id: str, document_id: str):
        if require_document(space_id, document_id).get("repository"):
            raise HTTPException(409, "请在代码 Wiki 页面删除仓库，或修改代码并重新同步")
        store.delete_document(space_id, document_id)
        objects.collect(store)

    @app.get("/api/spaces/{space_id}/documents/{document_id}/export")
    def export_document(space_id: str, document_id: str):
        document = require_document(space_id, document_id)
        return StreamingResponse(
            iter([document["content"]]),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{document_id}.md"'},
        )

    @app.get("/api/spaces/{space_id}/documents/{document_id}/original")
    def original_document(space_id: str, document_id: str):
        metadata = store.original_file(space_id, document_id)
        if not metadata:
            raise HTTPException(404, "文档原件不存在")
        response = objects.download(metadata["object_key"])

        def stream_original():
            try:
                yield from response.stream(64 * 1024)
            finally:
                response.close()
                response.release_conn()

        return StreamingResponse(
            stream_original(),
            media_type=metadata["content_type"],
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(metadata["filename"], safe=""),
                "Content-Length": str(metadata["size"]),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/spaces/{space_id}/import", status_code=201)
    async def import_document(space_id: str, file: UploadFile):
        require_space(space_id)
        try:
            data = await file.read(settings.max_upload_bytes + 1)
        finally:
            await file.close()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "文件超过大小限制（默认 10 MB）")
        filename = (file.filename or "文档").replace("\\", "/").split("/")[-1]
        try:
            title, content = await asyncio.to_thread(extract_text, filename, data)
        except ImportFailure as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(filename) > 255 or len(title) > 200:
            raise HTTPException(422, "文件名或标题过长")
        return await asyncio.to_thread(
            objects.upload, store, space_id, title, content, filename, data
        )

    @app.get("/api/spaces/{space_id}/search")
    def search(space_id: str, q: str = Query(min_length=1, max_length=500)):
        require_space(space_id)
        return store.search(space_id, q, 12)

    def require_repository(space_id, repository_id):
        repo = store.repository(space_id, repository_id)
        if not repo:
            raise HTTPException(404, "仓库不存在")
        return repo

    @app.get("/api/repository-settings")
    def repository_settings():
        return {
            "gitlab_configured": bool(settings.gitlab_url),
            "local_configured": bool(settings.repository_local_roots),
            "max_files": settings.repository_max_files,
        }

    @app.get("/api/spaces/{space_id}/repositories")
    def list_repositories(space_id: str):
        require_space(space_id)
        return store.repositories(space_id)

    @app.post("/api/spaces/{space_id}/repositories", status_code=201)
    def add_repository(space_id: str, body: RepositoryInput):
        require_space(space_id)
        if not body.name:
            raise HTTPException(422, "名称不能为空")
        try:
            source = repositories.git.validate(body.kind, body.source, body.ref)
        except (RepositoryFailure, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        repo = store.create_repository(space_id, body.name, body.kind, source, body.ref)
        store.enqueue_repository(space_id, repo["id"], "sync")
        return store.repository(space_id, repo["id"])

    @app.get("/api/spaces/{space_id}/repositories/{repository_id}")
    def get_repository(space_id: str, repository_id: str):
        repo = require_repository(space_id, repository_id)
        return dict(repo, files=store.repository_files(space_id, repository_id))

    @app.post("/api/spaces/{space_id}/repositories/{repository_id}/{job}", status_code=202)
    def repository_job(space_id: str, repository_id: str, job: str, force: bool = False):
        require_repository(space_id, repository_id)
        if job not in {"sync", "wiki"}:
            raise HTTPException(404, "任务不存在")
        if job == "wiki" and not settings.agent_enabled:
            raise HTTPException(422, "请先在 .env 配置模型服务；同步会自动生成结构 Wiki")
        try:
            store.enqueue_repository(space_id, repository_id, job, force=force)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "queued"}

    @app.get("/api/spaces/{space_id}/repositories/{repository_id}/source")
    def repository_source(
        space_id: str,
        repository_id: str,
        path: str = Query(max_length=512),
        snapshot_id: str | None = None,
        start_line: int = Query(default=1, ge=1),
        end_line: int = Query(default=200, ge=1),
    ):
        require_repository(space_id, repository_id)
        if end_line < start_line:
            raise HTTPException(422, "结束行不得小于开始行")
        try:
            return repositories.source(
                space_id, repository_id, path, snapshot_id, start_line, end_line
            )
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc

    @app.delete("/api/spaces/{space_id}/repositories/{repository_id}", status_code=204)
    def remove_repository(space_id: str, repository_id: str):
        require_repository(space_id, repository_id)
        try:
            store.delete_repository(space_id, repository_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        objects.collect(store)

    @app.get("/api/spaces/{space_id}/conversations")
    def conversations(space_id: str):
        require_space(space_id)
        return store.conversations(space_id)

    @app.post("/api/spaces/{space_id}/conversations", status_code=201)
    def new_conversation(space_id: str):
        require_space(space_id)
        return store.create_conversation(space_id)

    @app.get("/api/spaces/{space_id}/conversations/{conversation_id}")
    def get_conversation(space_id: str, conversation_id: str):
        conversation = store.conversation(space_id, conversation_id)
        if not conversation:
            raise HTTPException(404, "对话不存在")
        return conversation

    @app.post("/api/spaces/{space_id}/conversations/{conversation_id}/chat")
    async def chat(space_id: str, conversation_id: str, body: QuestionInput):
        if conversation_id in active_conversations:
            raise HTTPException(409, "此对话中已有问题正在处理")
        conversation = await asyncio.to_thread(store.conversation, space_id, conversation_id)
        if not conversation:
            raise HTTPException(404, "对话不存在")
        # No await between membership check and acquisition after the lookup.
        if conversation_id in active_conversations:
            raise HTTPException(409, "此对话中已有问题正在处理")
        active_conversations.add(conversation_id)

        async def stream():
            try:
                async with agent_slots:
                    async for event in answer_events(
                        store, settings, space_id, conversation, body.question
                    ):
                        yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            except Exception as exc:
                logger.warning("Chat failed (%s)", type(exc).__name__)
                yield (
                    "data: "
                    + json.dumps({"type": "error", "message": event_error(exc)}, ensure_ascii=False)
                    + "\n\n"
                )
            finally:
                active_conversations.discard(conversation_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
