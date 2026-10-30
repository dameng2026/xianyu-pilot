import asyncio
import logging
import re
import time

from app.services.qq_bridge.client_mock import MockConnector
from app.services.qq_bridge.contracts import Binding, Incoming, Query, SendStatus
from app.services.qq_bridge.policy import rejection_reason
from app.services.qq_bridge.result_matcher import match_result

from .regex_worker import extract
from .storage import Conflict
from .qq_connector import OneBotClient, ConnectorError, event_state

logger = logging.getLogger(__name__)


class WorkflowWorker:
    def __init__(self, repository, delay=0.25):
        self.repo, self.delay, self.failure = repository, delay, False

    def binding(self, row):
        account, service = row["snapshot"]["account"], row["snapshot"]["service"]
        return Binding(account["identity"], service["target"], service["target"].split(":")[-1])

    def live_client(self, row):
        account = row["snapshot"]["account"]
        current, credentials = self.repo.account_private(account["id"])
        if current["generation"] != account["generation"] or not current["verified"] or current["status"] != "online":
            raise ConnectorError("QQ 账号已变化或掉线，请用户重新核对")
        if time.time() - event_state(self.repo, account["id"])["last_seen"] >= 90:
            raise ConnectorError("QQ 事件心跳中断，暂停提交")
        client = OneBotClient(account["endpoint"], credentials.get("api_token", ""))
        target = row["snapshot"]["service"]["target"]
        if target not in {item["conversation_id"] for item in client.friends(account["identity"])}:
            raise ConnectorError("快照目标不是当前账号已核对的好友")
        return client

    def live_submit(self, row):
        client = self.live_client(row)
        account, service = row["snapshot"]["account"], row["snapshot"]["service"]
        with self.repo.store.connect() as db:
            cursor = db.execute("SELECT coalesce(max(id),0) FROM wf_connector_events WHERE tenant_id=? AND account_id=?", (self.repo.tenant, account["id"])).fetchone()[0]
        runtime = {"event_cursor": cursor, "deadline": time.time() + service["timeout_seconds"]}
        text = self.repo.render_request(row)
        if not 1 <= len(text) <= 4000:
            raise ConnectorError("渲染后的 QQ 请求长度超限")
        self.repo.checkpoint(row["id"], runtime)
        current, _ = self.repo.account_private(account["id"])
        if current["generation"] != account["generation"] or not current["verified"]:
            raise ConnectorError("账号授权在发送前变化，已停止")
        try:
            sent = client.call("send_private_msg", {"user_id": int(service["target"].split(":")[-1]),
                                                   "message": [{"type": "text", "data": {"text": text}}]})
            if not isinstance(sent, dict): raise ConnectorError("发送回执格式无效")
            message_id = str(sent.get("message_id", ""))
            if not re.fullmatch(r"-?[0-9]{1,30}", message_id):
                raise ConnectorError("缺少可核对的消息回执")
            return "confirmed", {"source": "onebot", "message_id": message_id}, runtime
        except ConnectorError:
            return "unknown", {"source": "onebot", "reason": "发送回执不明，禁止重发"}, runtime

    async def process_one(self):
        work = await asyncio.to_thread(self.repo.claim)
        if not work:
            return False
        row, stage = work
        if stage in {"delivery", "shipping"}:
            if stage == "delivery":
                try:
                    await asyncio.to_thread(self.repo.render_delivery, row)
                except (KeyError, ValueError):
                    await asyncio.to_thread(self.repo.update_runtime, row["id"], stage, outcome="failed", receipt={"reason": "交付模板参数不完整，尚未发送"})
                    return True
            await asyncio.sleep(self.delay)
            await asyncio.to_thread(self.repo.update_runtime, row["id"], stage, outcome="confirmed",
                                    receipt={"simulated": True, "source": "local_simulator", "operation_id": self.repo.step(row, stage)["operation_id"]})
            return True
        binding = self.binding(row)
        service = row["snapshot"]["service"]
        if stage == "qq":
            if row["snapshot"]["account"]["driver"] == "onebot":
                try:
                    outcome, receipt, runtime = await asyncio.to_thread(self.live_submit, row)
                except (ConnectorError, Conflict):
                    outcome, receipt, runtime = "failed", {"source": "onebot", "reason": "接入、授权或计划未就绪，未自动重试"}, None
            else:
                connector = MockConnector(binding, self.delay)
                try:
                    receipt_object = await connector.submit(Query(row["id"], self.repo.render_request(row), row["scenario"]))
                except (ValueError, KeyError):
                    await asyncio.to_thread(self.repo.update_runtime, row["id"], "qq", outcome="failed", receipt={"reason": "请求模板或正文无效，尚未发送"})
                    return True
                outcome = {SendStatus.CONFIRMED: "confirmed", SendStatus.REFUSED: "failed", SendStatus.UNKNOWN: "unknown"}[receipt_object.status]
                receipt = {"simulated": True, "source": "local_simulator"}
                runtime = {"deadline": time.time() + service["timeout_seconds"]}
            await asyncio.to_thread(self.repo.update_runtime, row["id"], "qq", outcome=outcome, receipt=receipt, runtime=runtime)
            return True
        if time.time() >= row.get("qq_runtime", {}).get("deadline", 0):
            await asyncio.to_thread(self.repo.update_runtime, row["id"], "qq", outcome="unknown", receipt={"reason": "等待结果超时，可能已扣费，不得自动重查"})
            return True
        query = Query(row["id"], self.repo.render_request(row), row["scenario"])
        if row["snapshot"]["account"]["driver"] == "mock":
            connector = MockConnector(binding, self.delay)
            async for event in connector.receive(query):
                await asyncio.to_thread(self.repo.record_incoming, row["id"], event, rejection_reason(binding, query, event))
            result = match_result(binding, query, await asyncio.to_thread(self.repo.incoming, row["id"]))
            if result is None:
                await asyncio.to_thread(self.repo.update_runtime, row["id"], "qq", outcome="unknown", receipt={"simulated": True, "reason": "模拟回复超时，需核对"})
            else:
                await self.finish_result(row, result)
        else:
            await self.receive_live(row, binding)
        return True

    async def finish_result(self, row, result):
        service = row["snapshot"]["service"]
        if service["result_mode"] == "regex":
            try:
                values = await asyncio.to_thread(extract, service["result_pattern"], result, complete=row["snapshot"]["account"]["driver"] == "onebot")
            except ValueError:
                values = None
            if not values:
                await asyncio.to_thread(self.repo.update_runtime, row["id"], "qq", outcome="unknown", receipt={"reason": "结果不符合完整提取规则，转人工"})
                return
            result = values.get("result") or values.get("value") or "\n".join(values.values())
        await asyncio.to_thread(self.repo.update_runtime, row["id"], "qq", outcome="confirmed", result=result,
                                receipt={"source": row["snapshot"]["account"]["driver"], "simulated": row["snapshot"]["account"]["driver"] == "mock"})

    async def receive_live(self, row, binding):
        try:
            account = row["snapshot"]["account"]
            current, _ = await asyncio.to_thread(self.repo.account_private, account["id"])
            state = await asyncio.to_thread(event_state, self.repo, account["id"])
            runtime = row["qq_runtime"]
            if current["generation"] != account["generation"] or not current["verified"] or time.time() - state["last_seen"] >= 90:
                raise ConnectorError("QQ 账号或签名事件通道已变化")
            if runtime["event_cursor"] < state["dropped_through"]:
                raise ConnectorError("事件缓存有缺口，不能推断结果完整")
            with self.repo.store.connect() as db:
                messages = [dict(item) for item in db.execute("SELECT * FROM wf_connector_events WHERE tenant_id=? AND account_id=? AND generation=? AND id>? ORDER BY id LIMIT 50",
                           (self.repo.tenant, account["id"], account["generation"], runtime["event_cursor"]))]
            for message in messages:
                receipt = self.repo.step(row, "qq")["receipt"] or {}
                correlated = row["id"] in message["content"] or (message["reply_to"] and message["reply_to"] == receipt.get("message_id"))
                event = Incoming(f"onebot:{account['id']}:{message['message_key']}", binding.account_id,
                                 f"{binding.account_id}:direct:{message['sender_id']}", message["sender_id"],
                                 row["id"] if correlated else "unmatched", message["content"])
                rejected = rejection_reason(binding, Query(row["id"], "", "normal"), event)
                await asyncio.to_thread(self.repo.record_incoming, row["id"], event, rejected)
            if messages:
                runtime = {**runtime, "event_cursor": messages[-1]["id"]}
                await asyncio.to_thread(self.repo.checkpoint, row["id"], runtime)
            events = await asyncio.to_thread(self.repo.incoming, row["id"])
            if events:
                result = "\n".join(event.body for event in events)
                if row["snapshot"]["service"]["result_mode"] == "regex":
                    values = await asyncio.to_thread(extract, row["snapshot"]["service"]["result_pattern"], result, complete=True)
                    if values:
                        await self.finish_result(row, result)
                else:
                    await asyncio.to_thread(self.repo.partial_result, row["id"], result)
            await asyncio.sleep(0.2)
        except (ConnectorError, ValueError, Conflict):
            await asyncio.to_thread(self.repo.update_runtime, row["id"], "qq", outcome="unknown", receipt={"reason": "接收中断、授权变化或结果有歧义，需人工核对"})

    async def run(self):
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("工作流处理器停止 (%s)，未自动重试", type(exc).__name__)
                self.failure = True
                return
