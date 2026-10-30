import asyncio
import logging

from app.services.qq_bridge.contracts import Binding, Query, SendStatus
from app.services.qq_bridge.policy import rejection_reason
from app.services.qq_bridge.result_matcher import match_result

from .storage import Store

logger = logging.getLogger(__name__)


class DemoWorker:
    def __init__(self, store: Store, connector, binding: Binding):
        self.store, self.connector, self.binding = store, connector, binding
        self.failure = False

    async def process_one(self) -> bool:
        row = await asyncio.to_thread(self.store.claim)
        if row is None:
            return False
        query = Query(row["id"], row["input"], row["scenario"])
        if row["status"] == "sending":
            receipt = await self.connector.submit(query)
            if receipt.operation_id != query.operation_id:
                raise ValueError("发送回执请求编号不一致")
            if receipt.status != SendStatus.CONFIRMED:
                unknown = receipt.status == SendStatus.UNKNOWN
                await asyncio.to_thread(
                    self.store.transition, row["id"], "sending", "unknown" if unknown else "failed_safe",
                    "模拟发送结果不明，队列已暂停；不会自动重发" if unknown else "模拟连接器明确拒绝发送，任务已安全结束",
                    freeze=unknown,
                )
                return True
            await asyncio.to_thread(self.store.transition, row["id"], "sending", "waiting_reply", "模拟发送已确认，等待带请求编号的模拟回复")
        complete = False
        try:
            async with asyncio.timeout(8):
                async for event in self.connector.receive(query):
                    rejection = rejection_reason(self.binding, query, event)
                    await asyncio.to_thread(self.store.record_event, row["id"], event, rejection)
                    if complete:
                        continue
                    incoming = await asyncio.to_thread(self.store.incoming, row["id"])
                    result = match_result(self.binding, query, incoming)
                    if result is not None:
                        await asyncio.to_thread(self.store.transition, row["id"], "waiting_reply", "completed", "模拟回复匹配完成，结果已保存；未向闲鱼或 QQ 发送任何消息", result)
                        complete = True
        except TimeoutError:
            pass
        if not complete:
            await asyncio.to_thread(self.store.transition, row["id"], "waiting_reply", "timed_out", "模拟回复未完整到达，队列已暂停，请核对后继续", freeze=True)
        return True

    async def run(self):
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never log task input or turn a partly executed operation into a retry.
                logger.error("本地演示处理器停止 (%s)，请重启服务检查任务恢复状态", type(exc).__name__)
                self.failure = True
                return
