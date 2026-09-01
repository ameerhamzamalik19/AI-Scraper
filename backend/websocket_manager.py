from collections import defaultdict
from typing import DefaultDict, Set

from fastapi import WebSocket


class ChatConnectionManager:
    def __init__(self) -> None:
        self.rooms: DefaultDict[str, Set[WebSocket]] = defaultdict(set)

    async def join(self, chat_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.rooms[chat_id].add(websocket)

    def leave(self, chat_id: str, websocket: WebSocket) -> None:
        self.rooms[chat_id].discard(websocket)
        if not self.rooms[chat_id]:
            del self.rooms[chat_id]

    def is_joined(self, chat_id: str, websocket: WebSocket) -> bool:
        return websocket in self.rooms.get(chat_id, set())

    async def broadcast(self, chat_id: str, message: dict) -> None:
        disconnected = []
        for websocket in self.rooms.get(chat_id, set()):
            try:
                await websocket.send_json({"type": "message", "message": message})
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.leave(chat_id, websocket)


chat_connection_manager = ChatConnectionManager()