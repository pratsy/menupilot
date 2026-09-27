import json
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.agent.orchestrator import run_turn
from app.db.session import init_db
from app.logging_config import configure_logging
from app.schemas import ChatRequest

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Euro Food Finder")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@app.on_event("startup")
def _startup() -> None:
    init_db()
    logger.info("Euro Food Finder started")


@app.post("/api/chat")
def chat(req: ChatRequest):
    logger.info("Chat request: session=%s message=%r", req.session_id, req.message)

    def event_stream():
        try:
            for event in run_turn(req.session_id, req.message):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.exception("Unhandled error in chat turn for session %s", req.session_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
