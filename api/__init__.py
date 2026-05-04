# api/__init__.py
from fastapi import FastAPI
from api.webhooks import router as webhooks_router

app = FastAPI(title="Aegis Webhook Receiver", version="1.0.0")
app.include_router(webhooks_router)

@app.get("/health")
async def health():
    return {"status": "ok"}