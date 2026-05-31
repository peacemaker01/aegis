# api/__init__.py
from fastapi import FastAPI

app = FastAPI(title="Aegis Security API", version="2.0.0", description="Aegis smart contract security API")

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}

# Include v1 router
from api.v1 import router as v1_router
app.include_router(v1_router)

from fastapi import Request, HTTPException
from core.cryptomus import verify_raw_webhook_signature