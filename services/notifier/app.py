import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="Omni-Trader Notifier")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REGION = os.getenv("AWS_REGION", "us-east-1")

class Alert(BaseModel):
    topic: str
    message: str

@app.post("/notify")
async def send_notification(alert: Alert):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise HTTPException(status_code=500, detail="Telegram credentials not configured")
    
    formatted_msg = f"[{REGION}] {alert.topic}: {alert.message}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "chat_id": CHAT_ID,
            "text": formatted_msg,
            "parse_mode": "Markdown"
        })
    
    return {"status": "sent"}

@app.get("/health")
def health():
    return {"status": "healthy", "region": REGION}
