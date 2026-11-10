from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .clips import MAX_IMAGE_BYTES, add_text_clip, store_image
from .collage import CollageService
from .config import ROOT, Settings
from .db import Database
from .errors import AppError, describe
from .llm import LLM, OpenAICompatLLM
from .poems import POETS, PoemService, style_of
from .posters import ImageGenerator, OpenAICompatImages, PosterService
from .quotes import Wikiquote
from .tmdb import TMDB
from .websearch import WebSearch

log = logging.getLogger("movie_diary")
STATIC = ROOT / "static"


class EntryCreate(BaseModel):
    tmdb_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    original_title: str = ""
    year: int | None = None
    director: str = ""
    overview: str = ""
    poster_url: str = ""
    genres: list[str] = []
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=2000)
    tags: list[str] = []
    watched_on: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    status: Literal["watched", "wishlist"] = "watched"


class EntryPatch(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    watched_on: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    status: Literal["watched", "wishlist"] | None = None


class ClipIn(BaseModel):
    kind: Literal["quote"] = "quote"
    text: str = Field(min_length=1, max_length=2000)
    note: str = Field(default="", max_length=200)


class PoemIn(BaseModel):
    style: str = Field(min_length=1, max_length=40)


class PoemSave(BaseModel):
    style: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=100)
    body: str = Field(min_length=1, max_length=3000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class PosterIn(BaseModel):
    quote: str = Field(min_length=1, max_length=200)


class CollageIn(BaseModel):
    quotes: list[str] = Field(min_length=1, max_length=4)


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error_code": code, "error": message}, status_code=status)


def create_app(settings: Settings | None = None, llm: LLM | None = None,
               images: ImageGenerator | None = None, tmdb: TMDB | None = None,
               search: WebSearch | None = None, wikiquote: Wikiquote | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)
    llm = llm or OpenAICompatLLM(settings)
    tmdb = tmdb or TMDB(settings)
    search = search or WebSearch(settings)
    poster_service = PosterService(settings, db, llm, images or OpenAICompatImages(settings))
    agent = Agent(settings, db, llm, tmdb, search, poster_service)
    poem_service = PoemService(llm, db)
    collage_service = CollageService(settings, db, tmdb)
    wikiquote = wikiquote or Wikiquote()

    app = FastAPI(title="Movie Diary")
    settings.posters_dir.mkdir(parents=True, exist_ok=True)
    settings.clips_dir.mkdir(parents=True, exist_ok=True)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            log.error("%s error_code=%s %s", type(exc).__name__, exc.code, exc.message)
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"] if p != "body")
        return error_response("request_invalid", f"{where or 'request'}: {first['msg']}", 422)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled %s", type(exc).__name__)
        return error_response("internal_error", describe(exc), 500)

    def need_entry(entry_id: int) -> dict[str, Any]:
        entry = db.get_entry(entry_id)
        if entry is None:
            raise AppError("entry_not_found", f"No diary entry with id {entry_id}", 404)
        return entry

    def hydrate(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            ids = m["meta"].get("poster_ids", [])
            posters = [p for p in (db.get_poster(i) for i in ids) if p]
            out.append({**m, "posters": posters})
        return out

    # ---- meta --------------------------------------------------------
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {"llm": settings.llm_configured, "image": settings.image_configured,
                "tmdb": settings.tmdb_configured, "search": settings.search_provider}

    # ---- movies ------------------------------------------------------
    @app.get("/api/movies/search")
    async def movie_search(q: str, year: int | None = None) -> dict[str, Any]:
        if not q.strip():
            raise AppError("query_empty", "q must not be empty", 422)
        return {"results": await tmdb.search(q.strip(), year)}

    # ---- entries -----------------------------------------------------
    @app.get("/api/entries")
    def list_entries() -> dict[str, Any]:
        return {"entries": db.list_entries(status=None)}

    @app.get("/api/on-this-day")
    def on_this_day(on: str | None = None) -> dict[str, Any]:
        """Watched entries from the same month and day in earlier years."""
        try:
            day = date.fromisoformat(on) if on else date.today()
        except ValueError as exc:
            raise AppError("date_invalid", f"on must be YYYY-MM-DD, got {on!r}", 422) from exc
        suffix = f"-{day.month:02d}-{day.day:02d}"
        hits = [e for e in db.list_entries(1000)
                if len(e["watched_on"]) == 10 and e["watched_on"].endswith(suffix)
                and int(e["watched_on"][:4]) < day.year]
        hits.sort(key=lambda e: e["watched_on"], reverse=True)
        return {"date": day.isoformat(),
                "entries": [{**e, "years_ago": day.year - int(e["watched_on"][:4])} for e in hits]}

    @app.post("/api/entries", status_code=201)
    async def create_entry(body: EntryCreate) -> dict[str, Any]:
        data = body.model_dump()
        if body.tmdb_id and tmdb.configured and not body.overview:
            data.update({k: v for k, v in (await tmdb.details(body.tmdb_id)).items()
                         if k not in ("tmdb_id", "title")})
        dup = db.find_duplicate(data["title"], data["year"], data["tmdb_id"])
        if dup and dup["status"] == "wishlist" and body.status == "watched":
            promote = {"status": "watched", "watched_on": body.watched_on or date.today().isoformat()}
            promote.update({k: data[k] for k in ("rating", "note", "tags") if data[k] not in (None, "", [])})
            return db.update_entry(dup["id"], promote)
        if dup:
            raise AppError("entry_already_exists",
                           f"《{dup['title']}》 is already in the diary as entry {dup['id']}", 409)
        return db.create_entry(data)

    @app.get("/api/entries/{entry_id}")
    def get_entry(entry_id: int) -> dict[str, Any]:
        return {**need_entry(entry_id), "posters": db.list_posters(entry_id),
                "clips": db.list_clips(entry_id), "poems": db.list_poems(entry_id)}

    @app.patch("/api/entries/{entry_id}")
    def patch_entry(entry_id: int, body: EntryPatch) -> dict[str, Any]:
        patch = body.model_dump(exclude_unset=True)
        if not patch:
            raise AppError("request_invalid", "patch has no fields", 422)
        entry = db.update_entry(entry_id, patch)
        if entry is None:
            raise AppError("entry_not_found", f"No diary entry with id {entry_id}", 404)
        return entry

    @app.delete("/api/entries/{entry_id}", status_code=204)
    def delete_entry(entry_id: int) -> None:
        need_entry(entry_id)
        gone = db.delete_entry(entry_id)
        for name in gone["posters"]:
            (settings.posters_dir / name).unlink(missing_ok=True)
        for name in gone["clips"]:
            (settings.clips_dir / name).unlink(missing_ok=True)

    # ---- chat --------------------------------------------------------
    @app.get("/api/chat")
    def global_history() -> dict[str, Any]:
        return {"messages": hydrate(db.list_messages(None))}

    @app.get("/api/entries/{entry_id}/chat")
    def entry_history(entry_id: int) -> dict[str, Any]:
        need_entry(entry_id)
        return {"messages": hydrate(db.list_messages(entry_id))}

    @app.post("/api/chat")
    async def global_chat(body: ChatIn) -> dict[str, Any]:
        return await agent.reply(None, body.message)

    @app.post("/api/entries/{entry_id}/chat")
    async def entry_chat(entry_id: int, body: ChatIn) -> dict[str, Any]:
        need_entry(entry_id)
        return await agent.reply(entry_id, body.message)

    @app.delete("/api/chat", status_code=204)
    def clear_global_chat() -> None:
        db.clear_messages(None)

    @app.delete("/api/entries/{entry_id}/chat", status_code=204)
    def clear_entry_chat(entry_id: int) -> None:
        need_entry(entry_id)
        db.clear_messages(entry_id)

    # ---- posters -----------------------------------------------------
    @app.post("/api/entries/{entry_id}/posters", status_code=201)
    async def create_poster(entry_id: int, body: PosterIn) -> dict[str, Any]:
        return await poster_service.create(need_entry(entry_id), body.quote)

    @app.post("/api/entries/{entry_id}/collage", status_code=201)
    async def create_collage(entry_id: int, body: CollageIn) -> dict[str, Any]:
        return await collage_service.create(need_entry(entry_id), body.quotes)

    @app.get("/api/entries/{entry_id}/quote-candidates")
    async def quote_candidates(entry_id: int) -> dict[str, Any]:
        entry = need_entry(entry_id)
        titles: list[tuple[str, str]] = []
        english = ""
        if entry.get("tmdb_id"):
            english = await tmdb.original_title(entry["tmdb_id"])
        if english:
            titles.append(("en", english))
        if entry.get("original_title"):
            titles.append(("en", entry["original_title"]))
        titles += [("zh", entry["title"]), ("en", entry["title"])]
        found = await wikiquote.find(titles)
        saved = {c["text"] for c in db.list_clips(entry_id, "quote")}
        for g in found["groups"]:
            for q in g["quotes"]:
                q["saved"] = q["text"] in saved
        return {"groups": found["groups"], "tried": found["tried"]}

    @app.delete("/api/posters/{poster_id}", status_code=204)
    def delete_poster(poster_id: int) -> None:
        poster = db.get_poster(poster_id)
        if poster is None:
            raise AppError("poster_not_found", f"No poster with id {poster_id}", 404)
        db.delete_poster(poster_id)
        (settings.posters_dir / poster["filename"]).unlink(missing_ok=True)

    # ---- clips: quotes and screenshots -------------------------------
    @app.get("/api/clips")
    def all_clips(kind: Literal["quote", "shot"] | None = None) -> dict[str, Any]:
        titles = {e["id"]: e["title"] for e in db.list_entries(5000, status=None)}
        return {"clips": [{**c, "title": titles.get(c["entry_id"], "")} for c in db.list_clips(kind=kind)]}

    @app.post("/api/entries/{entry_id}/clips", status_code=201)
    def create_clip(entry_id: int, body: ClipIn) -> dict[str, Any]:
        need_entry(entry_id)
        return add_text_clip(db, entry_id, body.kind, body.text, body.note)

    @app.post("/api/entries/{entry_id}/clips/shot", status_code=201)
    async def create_shot(entry_id: int, request: Request, note: str = "") -> dict[str, Any]:
        need_entry(entry_id)
        if len(note) > 200:
            raise AppError("clip_too_long", f"note is {len(note)} characters, the limit is 200", 422)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
            raise AppError("image_too_large", f"Image is larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB", 413)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise AppError("image_too_large",
                               f"Image is larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB", 413)
        filename = store_image(bytes(data), settings.clips_dir, entry_id)
        return db.add_clip(entry_id, "shot", note=note.strip(), filename=filename)

    @app.delete("/api/clips/{clip_id}", status_code=204)
    def delete_clip(clip_id: int) -> None:
        clip = db.get_clip(clip_id)
        if clip is None:
            raise AppError("clip_not_found", f"No clip with id {clip_id}", 404)
        db.delete_clip(clip_id)
        if clip["filename"]:
            (settings.clips_dir / clip["filename"]).unlink(missing_ok=True)

    # ---- poems -------------------------------------------------------
    @app.get("/api/poem-styles")
    def poem_styles() -> dict[str, Any]:
        return {"styles": [{k: p[k] for k in ("key", "name", "tagline", "group")} for p in POETS]}

    @app.post("/api/entries/{entry_id}/poem")
    async def write_poem(entry_id: int, body: PoemIn) -> dict[str, Any]:
        return await poem_service.write(need_entry(entry_id), body.style)

    @app.post("/api/entries/{entry_id}/poems", status_code=201)
    def save_poem(entry_id: int, body: PoemSave) -> dict[str, Any]:
        need_entry(entry_id)
        style = style_of(body.style)
        if style is None:
            raise AppError("poem_style_unknown", f"Unknown poem style {body.style!r}", 422)
        text = body.body.strip()
        if not text:
            raise AppError("poem_empty", "body must not be empty", 422)
        if db.find_poem(entry_id, text):
            raise AppError("poem_already_saved", f"This poem is already saved for entry {entry_id}", 409)
        return db.add_poem(entry_id, style["key"], style["name"], body.title.strip(), text)

    @app.get("/api/poems")
    def all_poems() -> dict[str, Any]:
        titles = {e["id"]: e["title"] for e in db.list_entries(5000, status=None)}
        return {"poems": [{**p, "movie": titles.get(p["entry_id"], "")} for p in db.list_poems()]}

    @app.delete("/api/poems/{poem_id}", status_code=204)
    def delete_poem(poem_id: int) -> None:
        if db.get_poem(poem_id) is None:
            raise AppError("poem_not_found", f"No poem with id {poem_id}", 404)
        db.delete_poem(poem_id)

    app.mount("/media/posters", StaticFiles(directory=settings.posters_dir), name="posters")
    app.mount("/media/clips", StaticFiles(directory=settings.clips_dir), name="clips")

    if (STATIC / "index.html").is_file():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise AppError("route_not_found", f"No API route /{path}", 404)
            target = (STATIC / path).resolve()
            if path and target.is_file() and STATIC.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(STATIC / "index.html")

    return app


def app_factory() -> FastAPI:
    return create_app()
