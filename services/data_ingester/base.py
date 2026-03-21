import asyncio
import json
import websockets
from shared.risk import RiskManager

class BaseIngester:
    def __init__(self, uri: str):
        self.uri = uri
        self.active = True

    async def connect(self):
        while self.active:
            try:
                async with websockets.connect(self.uri) as websocket:
                    await self.handle_stream(websocket)
            except Exception as e:
                print(f"Connection lost: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def handle_stream(self, websocket):
        raise NotImplementedError
