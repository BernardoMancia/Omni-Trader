import asyncio
import json
import websockets

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
                await asyncio.sleep(5)

    async def handle_stream(self, websocket):
        raise NotImplementedError
