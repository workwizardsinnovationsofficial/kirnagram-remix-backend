from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from routes.remix import router as remix_router
from database import db
from firebase import verify_firebase_token
from config import print_startup_info  # ✅ NEW: Startup validation

import os

app = FastAPI(title="Kirnagram Remix Backend")

# ✅ STARTUP: Print configuration status
print("\n" + "="*60)
print("🚀 [STARTUP] Kirnagram Remix Backend Initializing")
print("="*60)
print_startup_info()
print("="*60 + "\n")

# ✅ SECURITY HEADERS MIDDLEWARE
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Allow cross-origin window operations for Google OAuth
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        return response

class UserActivityTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if request.method == "OPTIONS":
            return response

        path = request.url.path or ""
        if path.startswith("/remix"):
            try:
                auth_header = request.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header.split(" ")[1]
                    decoded = verify_firebase_token(token)
                    user_id = decoded["uid"]
                    # Track activity
                    import asyncio
                    asyncio.create_task(db.users.update_one(
                        {"_id": user_id},
                        {"$set": {"last_activity": {"timestamp": "now", "path": path}}}
                    ))
            except Exception:
                pass  # Don't fail request if tracking fails

        return response

# Middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(UserActivityTrackingMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trusted hosts
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # In production, specify your domains
)

# Include routers
app.include_router(remix_router)

@app.get("/")
async def root():
    return {"message": "Kirnagram Remix Backend API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)