# api/__init__.py
from fastapi import FastAPI

app = FastAPI(title="Aegis Webhook Receiver", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok"}