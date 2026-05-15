"""
WebSocket Manager - in-memory version (no Redis required).
Works for single-process server deployments.
"""

import logging
from collections import defaultdict
from typing import DefaultDict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        self.rooms: DefaultDict[str, set[WebSocket]] = defaultdict(set)

    async def startup(self):
        logger.info("WebSocket manager ready (in-memory mode)")

    async def shutdown(self):
        for room, sockets in self.rooms.items():
            for ws in list(sockets):
                try:
                    await ws.close()
                except Exception:
                    pass
        logger.info("WebSocket manager shut down")

    async def connect(self, websocket: WebSocket, room: str):
        await websocket.accept()
        self.rooms[room].add(websocket)
        logger.info("WS connected to room '%s' - total: %d", room, len(self.rooms[room]))

    async def disconnect(self, websocket: WebSocket, room: str):
        self.rooms[room].discard(websocket)
        logger.info("WS disconnected from room '%s' - remaining: %d", room, len(self.rooms[room]))

    async def broadcast_event(self, room: str, event_type: str, payload: dict):
        data = {"room": room, "type": event_type, "payload": payload}
        await self._fanout(room, data)

    async def _fanout(self, room: str, data: dict):
        dead: list[WebSocket] = []
        for ws in list(self.rooms.get(room, set())):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.rooms[room].discard(ws)


ws_manager = WebSocketManager()