# main.py
# FastAPI application entry point for FieldCapture Web.
#
# Run with:
#   python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
#
# The --host 0.0.0.0 flag makes the server reachable from your Android phone
# on the same Wi-Fi network. Find your laptop's LAN IP with:
#   ipconfig   (look for IPv4 Address under your Wi-Fi adapter)
# Then open http://<your-ip>:8000 in Android Chrome.

import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.routes import session, capture, job_record

app = FastAPI(
    title="FieldCapture Web",
    description="Property claims field capture — web version of the iOS DAT 1.0 app",
    version="1.0.0",
)

@app.on_event("startup")
async def startup_message():
    import socket
    hostname = socket.gethostname()
    try:
        lan_ip = socket.gethostbyname(hostname)
    except Exception:
        lan_ip = "unknown"
    print("\n" + "="*55)
    print("  FieldCapture Web is running")
    print(f"  Local:   http://localhost:8000")
    print(f"  Network: http://{lan_ip}:8000")
    print("  Open either URL in your browser")
    print("="*55 + "\n")

# Allow requests from the Android browser (any origin on local network)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(session.router)
app.include_router(capture.router)
app.include_router(job_record.router)

# Serve the frontend from /frontend
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
