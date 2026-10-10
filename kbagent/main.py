import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .agent import answer_events, event_error
from .config import Settings
from .importer import ImportFailure, extract_text
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


class LinkBinding(BaseModel):
    target_id: str = Field(min_length=1, max_length=64)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.data_dir)
    active_conversations: set[str] = set()
    agent_slots = asyncio.Semaphore(4)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield

    app = FastAPI(title="KB Agent", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

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
        return {"status": "ok", "database": "ok"}

    @app.get("/api/settings")
    def status():
        return {
            "mode": "agent" if settings.agent_enabled else "retrieval",
            "model": settings.model if settings.agent_enabled else None,
            "sdk": "deepagents",
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
        store.delete_space(space_id)

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
        require_document(space_id, document_id)
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
        try:
            store.bind_link(space_id, document_id, link_id, body.target_id)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        return {"status": "resolved", "target_id": body.target_id}

    @app.delete("/api/spaces/{space_id}/documents/{document_id}", status_code=204)
    def delete_document(space_id: str, document_id: str):
        require_document(space_id, document_id)
        store.delete_document(space_id, document_id)

    @app.get("/api/spaces/{space_id}/documents/{document_id}/export")
    def export_document(space_id: str, document_id: str):
        document = require_document(space_id, document_id)
        return StreamingResponse(
            iter([document["content"]]),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{document_id}.md"'},
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
        return await asyncio.to_thread(store.save_document, space_id, title, content, filename)

    @app.get("/api/spaces/{space_id}/search")
    def search(space_id: str, q: str = Query(min_length=1, max_length=500)):
        require_space(space_id)
        return store.search(space_id, q, 12)

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
