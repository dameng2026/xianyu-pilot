"""An entirely local simulator. No networking, QQ SDK or account credentials."""
import asyncio
from dataclasses import replace

from .contracts import Binding, Incoming, Query, Receipt, SendStatus
from .policy import validate_query


class MockConnector:
    def __init__(self, binding: Binding, delay: float = 0.45):
        self.binding = binding
        self.delay = delay

    def health(self) -> dict:
        return {"mode": "mock", "bound": True, "real_qq_connected": False}

    def capabilities(self) -> dict:
        return {"text_only": True, "explicit_correlation": True, "max_concurrency": 1}

    async def submit(self, query: Query) -> Receipt:
        validate_query(query)
        await asyncio.sleep(self.delay)
        status = {"refused": SendStatus.REFUSED, "unknown": SendStatus.UNKNOWN}.get(
            query.scenario, SendStatus.CONFIRMED
        )
        return Receipt(query.operation_id, status)

    async def receive(self, query: Query):
        """Stable event IDs let a restarted receiver safely replay local events."""
        if query.scenario == "timeout":
            await asyncio.sleep(self.delay * 6)
            return
        chunks = [f"【模拟结果】已处理演示请求：{query.text}"]
        if query.scenario == "split":
            chunks = ["【模拟结果 · 第 1 段】请求已接收。", f"【第 2 段】演示内容：{query.text}"]
        for index, body in enumerate(chunks, 1):
            event = Incoming(
                f"{query.operation_id}:part:{index}", self.binding.account_id,
                self.binding.conversation_id, self.binding.bot_id,
                query.operation_id, body, index, len(chunks),
            )
            await asyncio.sleep(self.delay)
            if query.scenario == "wrong_sender":
                yield replace(event, event_id=event.event_id + ":other", sender_id="demo-stranger")
            yield event
            if query.scenario == "duplicate":
                yield event

    async def reconcile(self, query: Query) -> Receipt:
        # Unknown is deliberately not converted to a success, nor sent again.
        status = {"unknown": SendStatus.UNKNOWN, "refused": SendStatus.REFUSED}.get(
            query.scenario, SendStatus.CONFIRMED
        )
        return Receipt(query.operation_id, status)

    async def close(self) -> None:
        pass
