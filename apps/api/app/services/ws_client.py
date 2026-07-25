"""
闲鱼 WebSocket 连接管理器。

管理到 wss://wss-goofish.dingtalk.com/ 的 WebSocket 连接生命周期：
- 连接建立
- /reg 注册
- /r/SyncStatus/ackDiff 同步
- 消息接收与 ACK
- 15秒心跳保活
- 断线自动重连
"""
import asyncio
import json
import logging
import ssl
import threading
import time
from typing import Any, Callable, Optional

import websocket

from .ws_token import (
    get_ws_token,
    get_ws_token_with_refreshed_m_h5_tk,
    extract_m_h5_tk_from_cookie,
    generate_device_id,
    invalidate_cached_token,
)
from ..core.background_tasks import spawn_background_task
from ..core.cookie_crypto import encrypt_cookie_for_storage
from .ws_protocol import (
    _normalize_goofish_target,
    build_reg_message,
    build_sync_message,
    build_ack_message,
    build_heartbeat_message,
    build_send_message,
    build_send_image_message,
    generate_mid,
    parse_sync_package,
    parse_send_response,
    normalize_peer_name,
)
from .ws_sse import broadcaster

logger = logging.getLogger(__name__)

WS_URL = "wss://wss-goofish.dingtalk.com/"
HEARTBEAT_INTERVAL = 15  # 心跳间隔（秒）
RECONNECT_DELAY = 5  # 重连延迟（秒）
MESSAGE_TIMEOUT = 10  # 发送消息超时（秒）


IMAGE_MESSAGE_ACK_TIMEOUT = 10  # 图片 ACK 可能延迟，与文本消息保持一致的超时窗口


# 自动滑块求解去重状态已迁移至 captcha_solver 模块（should_auto_solve / mark_auto_solve_started），
# 由 ws_client / cookie_token_refresher 等多个触发源共享，避免同账号并发求解。
from .captcha_solver import should_auto_solve as _should_auto_solve, mark_auto_solve_started as _mark_auto_solve_started  # noqa: E402


async def _lookup_account_name_safe(account_id: int) -> str:
    """查询账号昵称，失败时回退为账号 ID 字符串。供通知文案使用。"""
    try:
        from ..core.database import async_session
        from sqlalchemy import text as _sa_text
        async with async_session() as db:
            row = (await db.execute(
                _sa_text(
                    "SELECT nickname FROM xianyu_account "
                    "WHERE id = :aid AND deleted = 0 LIMIT 1"
                ),
                {"aid": account_id},
            )).mappings().first()
            if row and row.get("nickname"):
                return str(row["nickname"])
    except Exception as exc:
        logger.debug(
            "查询账号昵称失败，回退为账号ID errorType=%s",
            type(exc).__name__,
        )
    return str(account_id)


class _ThreadedWebSocketAdapter:
    """基于 websocket-client 的最小异步适配层。"""

    def __init__(self, account_id: int, url: str, headers: dict[str, str]):
        self.account_id = account_id
        self.url = url
        self.headers = headers
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_app: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._recv_queue: Optional[asyncio.Queue[str]] = None
        self._open_event: Optional[asyncio.Event] = None
        self._close_event: Optional[asyncio.Event] = None
        self._connected = False
        self._close_code: Optional[int] = None
        self._close_reason: Optional[str] = None
        self._connect_error: Optional[Exception] = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, timeout: float = 10.0):
        self._loop = asyncio.get_running_loop()
        self._recv_queue = asyncio.Queue()
        self._open_event = asyncio.Event()
        self._close_event = asyncio.Event()
        self._connected = False
        self._close_code = None
        self._close_reason = None
        self._connect_error = None

        header_lines = [f"{key}: {value}" for key, value in self.headers.items()]
        self._ws_app = websocket.WebSocketApp(
            self.url,
            header=header_lines,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=self._run_forever,
            name=f"xianyu-ws-{self.account_id}",
            daemon=True,
        )
        self._thread.start()

        try:
            await asyncio.wait_for(self._open_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise TimeoutError(f"WS 连接超时 accountId={self.account_id}")

        if self._connect_error and not self._connected:
            raise self._connect_error

    def _run_forever(self):
        try:
            assert self._ws_app is not None
            self._ws_app.run_forever(
                ping_interval=0,
                ping_timeout=None,
                skip_utf8_validation=False,
                sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                origin="https://www.goofish.com",
                host="wss-goofish.dingtalk.com",
                suppress_origin=False,
            )
        except Exception as exc:
            self._connect_error = exc
            self._notify_open()
            self._notify_close()

    def _on_open(self, _ws_app):
        self._connected = True
        logger.info("WS 底层连接已打开 accountId=%d", self.account_id)
        self._notify_open()

    def _on_message(self, _ws_app, message):
        if self._loop and self._recv_queue:
            asyncio.run_coroutine_threadsafe(self._recv_queue.put(message), self._loop)

    def _on_error(self, _ws_app, error):
        if not self._connected and self._connect_error is None:
            self._connect_error = error if isinstance(error, Exception) else RuntimeError(str(error))
            self._notify_open()
        logger.warning(
            "WS 底层错误 accountId=%d errorType=%s",
            self.account_id,
            type(error).__name__,
        )

    def _on_close(self, _ws_app, close_status_code, close_msg):
        self._connected = False
        self._close_code = close_status_code
        self._close_reason = close_msg
        logger.info(
            "WS 底层连接已关闭 accountId=%d code=%s reasonPresent=%s",
            self.account_id,
            close_status_code,
            bool(close_msg),
        )
        # 必须在 recv_queue 中放入 None，否则 _message_loop 会永久阻塞在
        # recv_queue.get() 上，_connect 的 finally 块无法执行，_connected
        # 保持 True，后续消息发送到死连接上导致超时。
        if self._loop and self._recv_queue:
            asyncio.run_coroutine_threadsafe(self._recv_queue.put(None), self._loop)
        self._notify_close()
        self._notify_open()
        if self._loop and self._recv_queue:
            asyncio.run_coroutine_threadsafe(self._recv_queue.put(None), self._loop)
        # 账号掉线通知（失败不影响主流程；仅在非主动关闭时通知，避免正常重连刷屏）
        if self._loop and close_status_code not in (1000, 1001, None):
            try:
                from .notify_dispatcher import notify_account_offline
                reason = f"code={close_status_code}"
                asyncio.run_coroutine_threadsafe(
                    notify_account_offline(self.account_id, reason),
                    self._loop,
                )
            except Exception as exc:
                logger.debug(
                    "账号掉线通知异常，忽略 errorType=%s",
                    type(exc).__name__,
                )

    def _notify_open(self):
        if self._loop and self._open_event:
            self._loop.call_soon_threadsafe(self._open_event.set)

    def _notify_close(self):
        if self._loop and self._close_event:
            self._loop.call_soon_threadsafe(self._close_event.set)

    async def send(self, data: str):
        if not self._ws_app or not self._connected:
            raise RuntimeError("WebSocket 未连接")
        await asyncio.to_thread(self._ws_app.send, data)

    async def recv(self) -> str:
        if not self._recv_queue:
            raise RuntimeError("WebSocket 接收队列未初始化")
        message = await self._recv_queue.get()
        if message is None:
            raise ConnectionError(
                f"WebSocket 连接已关闭 code={self._close_code}"
            )
        return message

    async def close(self):
        ws_app = self._ws_app
        thread = self._thread
        self._connected = False

        if ws_app:
            await asyncio.to_thread(ws_app.close)

        if self._close_event:
            try:
                await asyncio.wait_for(self._close_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

        if thread and thread.is_alive():
            await asyncio.to_thread(thread.join, 5)

        self._ws_app = None
        self._thread = None


class XianyuWebSocketClient:
    """闲鱼 WebSocket 客户端。

    每个账号一个实例，管理独立的 WebSocket 连接。
    """

    def __init__(
        self,
        account_id: int,
        cookie_str: str,
        m_h5_tk: str,
        unb: str,
        on_message_callback: Optional[Callable] = None,
    ):
        self.account_id = account_id
        self.cookie_str = cookie_str
        self.m_h5_tk = m_h5_tk
        self.unb = unb

        # 连接状态
        self._ws: Optional[_ThreadedWebSocketAdapter] = None
        self._running = False
        self._connected = False
        self._registered = False
        self.phase = "stopped"
        self._sid: Optional[str] = None  # 注册后获取的 sid
        self._access_token: Optional[str] = None
        self._tasks: list[asyncio.Task] = []

        # 发送消息的 Future 映射 (uuid -> asyncio.Future)
        self._send_futures: dict[str, asyncio.Future] = {}

        # 同步状态
        self._sync_pts = 0  # 当前同步基点时间戳
        self._sync_high_pts = 0  # 当前同步高点水印

        # 外部回调（如消息存储）
        self.on_message_callback = on_message_callback

        # 最近一次收到服务端消息的时间戳（用于检测连接是否静默断开）
        self._last_recv_time: float = time.time()

        # 诊断状态：前端连接管理页可据此区分"已提交但未连接"、
        # “Token/Cookie 异常”、“已注册并同步”等状态。
        self.phase = "created"
        self.last_error = ""

    @property
    def is_connected(self) -> bool:
        """是否已连接并注册（对标商业版 im_client.py:214-217 双重检查）"""
        return self._connected and self._registered

    @property
    def device_id(self) -> str:
        """生成 /reg 使用的 did。

        与 accessToken 获取阶段（ws_token._call_token_api）的 deviceId 保持一致，
        统一使用 generate_device_id(unb) 生成与账号绑定的固定 UUID。
        这样 token 刷新时 deviceId 保持稳定，服务端识别为"已知设备的正常令牌更新"，
        避免因 device 身份变化触发 FAIL_SYS_USER_VALIDATE 风控。
        """
        if self.unb:
            return generate_device_id(self.unb)
        logger.error("device_id: unb 为空，无法生成 did accountId=%d", self.account_id)
        return ""

    async def start(self):
        """启动 WebSocket 连接。"""
        if self._running:
            logger.warning("WS 客户端 accountId=%d 已在运行", self.account_id)
            return

        self._running = True
        self.phase = "starting"
        self.last_error = ""
        logger.info("WS 客户端启动 accountId=%d", self.account_id)

        # 启动连接任务
        task = asyncio.create_task(self._connect_loop())
        self._tasks.append(task)

    async def stop(self):
        """停止 WebSocket 连接。"""
        self._running = False
        self._connected = False
        self._registered = False
        self.phase = "stopped"

        # 关闭 WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # 取消所有任务
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        # 清理发送 Future
        for fut in self._send_futures.values():
            if not fut.done():
                fut.set_exception(asyncio.CancelledError())
        self._send_futures.clear()

        logger.info("WS 客户端停止 accountId=%d", self.account_id)

    async def send_text_message(self, cid: str, to_id: str, text: str, persist: bool = True) -> dict[str, Any]:
        """Override: keep text-message ACK behavior unchanged.

        Args:
            persist: 发送成功后是否主动入库。AI 回复等已自行调用 save_chat_message
                     的场景应设为 False，避免重复入库。
        """
        if not self._connected or not self._ws:
            for _ in range(30):
                await asyncio.sleep(0.1)
                if self._connected and self._ws:
                    break
            else:
                return {"code": 503, "error": "WebSocket 尚未连接，请稍后重试"}

        if not self._sid:
            return {"code": 503, "error": "WebSocket 尚未完成注册，请稍后重试"}

        if not cid:
            return {"code": 422, "error": "会话 ID 不能为空"}
        if not to_id:
            return {"code": 422, "error": "接收者 ID 不能为空"}

        idle_secs = time.time() - self._last_recv_time
        if idle_secs > HEARTBEAT_INTERVAL * 2:
            logger.warning(
                "发送文本前检测到 WebSocket 长时间无入站数据 accountId=%d idleSeconds=%.0f",
                self.account_id,
                idle_secs,
            )
            return {"code": 503, "error": "WebSocket 连接可能已断开，请等待重连后再试"}

        from_id = f"{self.unb}@goofish" if self.unb else ""

        msg = build_send_message(cid, to_id, from_id, text, self._sid)
        msg_mid = msg["headers"]["mid"]
        sent_uuid = msg["body"][0]["uuid"]

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._send_futures[msg_mid] = fut

        try:
            logger.debug(
                "WS 发送文本消息 accountId=%d contentLength=%d",
                self.account_id,
                len(text),
            )
            await self._ws.send(json.dumps(msg, ensure_ascii=False))
            result = await asyncio.wait_for(fut, timeout=MESSAGE_TIMEOUT)
            # 纯 ACK 响应（{"code":200,"headers":{"mid":...}}）不含 uuid，
            # 注入发送时生成的 uuid 作为消息标识，确保前端乐观消息、SSE 广播、
            # 服务端持久化消息三者共享同一 identity，避免消息重复显示。
            if not result.get("uuid"):
                result = {**result, "uuid": sent_uuid}
            # 发送成功后主动入库（解决自己消息不显示的问题）
            if result.get("code") == 200 and persist:
                local_persisted = await self._persist_outbound_message(
                    cid,
                    to_id,
                    text=text,
                    content_type=1,
                )
                result = self._with_local_persistence_status(
                    result,
                    persisted=local_persisted,
                )
            return result
        except asyncio.TimeoutError:
            logger.error(
                "发送文本消息确认超时 accountId=%d connected=%s phase=%s",
                self.account_id,
                self._connected,
                getattr(self, "phase", "unknown"),
            )
            self.last_error = "发送文本消息确认超时（连接保持）"
            return {
                "code": 504,
                "error": "发送结果未确认，请先在闲鱼 App 核对，避免重复发送",
                "deliveryUnknown": True,
                "retrySafe": False,
            }
        except Exception as exc:
            logger.error(
                "发送文本消息异常 accountId=%d errorType=%s",
                self.account_id,
                type(exc).__name__,
            )
            return {"code": 500, "error": "发送消息失败，请稍后重试"}
        finally:
            self._send_futures.pop(msg_mid, None)

    async def send_image_message(
        self,
        cid: str,
        to_id: str,
        image_url: str,
        width: int = 800,
        height: int = 600,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Send an image and require a positive platform acknowledgement.

        ``persist=False`` is used by the durable manual-message coordinator,
        which persists the confirmed message in the same local workflow.  It
        prevents a second asynchronous write after the platform ACK.
        """
        if not self._connected or not self._ws:
            for _ in range(30):
                await asyncio.sleep(0.1)
                if self._connected and self._ws:
                    break
            else:
                return {"code": 503, "error": "WebSocket 尚未连接，请稍后重试"}

        if not self._sid:
            return {"code": 503, "error": "WebSocket 尚未完成注册，请稍后重试"}

        if not cid:
            return {"code": 422, "error": "会话 ID 不能为空"}
        if not to_id:
            return {"code": 422, "error": "接收者 ID 不能为空"}

        idle_secs = time.time() - self._last_recv_time
        if idle_secs > HEARTBEAT_INTERVAL * 2:
            logger.warning(
                "发送图片前检测到 WebSocket 长时间无入站数据 accountId=%d idleSeconds=%.0f",
                self.account_id,
                idle_secs,
            )
            return {"code": 503, "error": "WebSocket 连接可能已断开，请等待重连后再试"}

        from_id = f"{self.unb}@goofish" if self.unb else ""

        logger.info(
            "WS 发送图片消息 accountId=%d width=%d height=%d",
            self.account_id,
            width,
            height,
        )

        msg = build_send_image_message(
            cid,
            to_id,
            from_id,
            image_url,
            self._sid,
            width=width,
            height=height,
        )
        msg_mid = msg["headers"]["mid"]
        sent_uuid = msg["body"][0]["uuid"]

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._send_futures[msg_mid] = fut

        try:
            await self._ws.send(json.dumps(msg, ensure_ascii=False))
            result = await asyncio.wait_for(fut, timeout=IMAGE_MESSAGE_ACK_TIMEOUT)
            # 纯 ACK 响应不含 uuid，注入发送时生成的 uuid 作为消息标识，
            # 确保前端乐观消息、SSE 广播、服务端持久化消息三者共享同一 identity。
            if not result.get("uuid"):
                result = {**result, "uuid": sent_uuid}
            # 发送成功后主动入库（解决发送图片不显示的问题）
            if result.get("code") == 200 and persist:
                local_persisted = await self._persist_outbound_message(
                    cid,
                    to_id,
                    image_url=image_url,
                    width=width,
                    height=height,
                    content_type=2,
                )
                result = self._with_local_persistence_status(
                    result,
                    persisted=local_persisted,
                )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "发送图片消息确认超时 accountId=%d；状态未知且不会按成功入库",
                self.account_id,
            )
            return {
                "code": 504,
                "uuid": msg["body"][0]["uuid"],
                "mid": msg_mid,
                "error": "发送结果未确认，请先在闲鱼 App 核对，避免重复发送",
                "deliveryUnknown": True,
                "retrySafe": False,
            }
        except Exception as exc:
            logger.error(
                "发送图片消息异常 accountId=%d errorType=%s",
                self.account_id,
                type(exc).__name__,
            )
            return {"code": 500, "error": "发送图片失败，请稍后重试"}
        finally:
            self._send_futures.pop(msg_mid, None)

    @staticmethod
    def _with_local_persistence_status(
        result: dict[str, Any],
        *,
        persisted: bool,
    ) -> dict[str, Any]:
        annotated = dict(result)
        annotated["deliveryConfirmed"] = True
        annotated["localPersisted"] = bool(persisted)
        if not persisted:
            annotated["retrySafe"] = False
            annotated["warning"] = (
                "消息已确认发送，但本地记录保存失败；请刷新会话核对，勿重复发送"
            )
        return annotated

    async def _persist_outbound_message(
        self,
        cid: str,
        to_id: str,
        text: str = "",
        image_url: str = "",
        width: int = 0,
        height: int = 0,
        content_type: int = 1,
    ) -> bool:
        """发送消息成功后主动保存到数据库，避免依赖 IM 推送回环导致漏收。

        旧实现：发送消息后只等待 ACK，不入库。自己/AI 发的消息完全依赖 IM 服务器
        推送回来（sync package）才会入库，如果推送丢失则消息消失——这是
        "仅显示对方消息、不显示自己消息和 AI 回复"的核心根因。

        新实现：发送成功后立即构造 OUT 消息并调用 on_message_callback 入库。
        如果推送回环也来了，save_chat_message 的 message_uid/pnm_id 去重逻辑
        会处理重复，不会产生双份数据。
        """
        if not self.on_message_callback:
            logger.warning(
                "发送消息缺少本地持久化回调 accountId=%d",
                self.account_id,
            )
            return False

        from_id = f"{self.unb}@goofish" if self.unb else ""

        # sId 在协议中通常不带 @goofish 后缀，入库也以裸 sId 为准
        s_id_raw = cid
        if s_id_raw and s_id_raw.endswith("@goofish"):
            s_id_raw = s_id_raw[:-8]

        # messageTime 设为 0：不用本地时间（本地时间是"获取时间"不是"发送时间"）。
        # save_chat_message 兜底为 created_time（入库时间）作为临时值。
        # 当 IM 推送回环到来时（actualReceivers 包含自己），推送包带服务端时间戳（字段 5），
        # save_chat_message 的去重命中逻辑会用服务端时间戳更新 message_time。
        msg: dict[str, Any] = {
            "direction": "OUT",
            "senderUserId": from_id,
            "receiverUserId": to_id,
            "sId": s_id_raw,
            "msgContent": text,
            "contentType": content_type,
            "messageTime": 0,
            "pnmId": "",
            "xyGoodsId": "",
            "readStatus": 1,
        }

        # 图片消息需要携带完整图片结构，前端 extractImageMessageUrls 会从
        # completeMsg 中提取 imageUrls/images 字段，避免从 msgContent 推断失败。
        if content_type == 2 and image_url:
            msg["msgContent"] = ""
            msg["imageUrls"] = [image_url]
            msg["images"] = [{"url": image_url, "width": width, "height": height}]

        try:
            await self.on_message_callback(self.account_id, msg)
            return True
        except Exception as exc:
            logger.warning(
                "发送消息主动入库失败 accountId=%d errorType=%s",
                self.account_id,
                type(exc).__name__,
            )
            return False

    async def _connect_loop(self):
        """连接循环（自动重连）。"""
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = f"WebSocket 连接异常（{type(exc).__name__}）"
                self.phase = "error"
                logger.error(
                    "WS 连接异常 accountId=%d errorType=%s",
                    self.account_id,
                    type(exc).__name__,
                )

            if self._running:
                logger.info("WS 将在 %d 秒后重连 accountId=%d", RECONNECT_DELAY, self.account_id)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _send_and_wait(self, msg: dict[str, Any], timeout: float = MESSAGE_TIMEOUT) -> dict[str, Any]:
        if not self._connected or not self._ws:
            raise RuntimeError("WebSocket 未连接")

        headers = msg.setdefault("headers", {})
        msg_mid = str(headers.get("mid") or generate_mid())
        headers["mid"] = msg_mid

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._send_futures[msg_mid] = fut

        try:
            await self._ws.send(json.dumps(msg, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._send_futures.pop(msg_mid, None)

    async def list_conversations(
        self,
        start_timestamp: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if start_timestamp is None:
            start_timestamp = 9007199254740991

        response = await self._send_and_wait(
            {
                "lwp": "/r/Conversation/listNewestPagination",
                "headers": {"mid": generate_mid()},
                "body": [int(start_timestamp), max(int(limit or 20), 1)],
            },
            timeout=max(MESSAGE_TIMEOUT, 8),
        )
        return response.get("body") or {}

    async def list_messages(
        self,
        cid: str,
        start_timestamp: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if start_timestamp is None:
            start_timestamp = 9007199254740991

        response = await self._send_and_wait(
            {
                "lwp": "/r/MessageManager/listUserMessages",
                "headers": {"mid": generate_mid()},
                "body": [
                    _normalize_goofish_target(cid),
                    False,
                    int(start_timestamp),
                    max(int(limit or 20), 1),
                    False,
                ],
            },
            timeout=max(MESSAGE_TIMEOUT, 8),
        )
        return response.get("body") or {}

    def _ensure_cookie_has_mh5tk(self):
        """确保 self.cookie_str 中包含 _m_h5_tk 且与 self.m_h5_tk 一致。

        关键：WebSocket 升级时会发送 Cookie 头，服务端会检查 cookie 中的 _m_h5_tk
        是否与 accessToken 匹配。如果 cookie 中不含 _m_h5_tk 或值不匹配，
        服务端会静默丢弃连接后的所有消息。

        注意：self.m_h5_tk 此时已是最新值（_refresh_token 已更新），
        accessToken 是使用 self.m_h5_tk 生成的。因此 cookie 中必须包含
        完全相同的 _m_h5_tk 值，不能使用 cookie 中旧的/不同的值。
        """
        import re

        cookie_m_h5_tk = extract_m_h5_tk_from_cookie(self.cookie_str)

        logger.info(
            "_ensure_cookie_has_mh5tk: accountId=%d cookie_credential=%s token_credential=%s match=%s",
            self.account_id,
            bool(cookie_m_h5_tk),
            bool(self.m_h5_tk),
            bool(cookie_m_h5_tk and self.m_h5_tk and cookie_m_h5_tk == self.m_h5_tk),
        )

        if cookie_m_h5_tk and self.m_h5_tk and cookie_m_h5_tk != self.m_h5_tk:
            # cookie 中的 _m_h5_tk 与生成 accessToken 所用的不一致！
            # 必须修复 cookie，否则服务端会因 _m_h5_tk 不匹配而静默丢弃连接
            logger.warning(
                "_ensure_cookie_has_mh5tk: accountId=%d, cookie 中的 _m_h5_tk 与 "
                "生成 accessToken 所用的不同, 正在修复 cookie",
                self.account_id,
            )
            self.cookie_str = re.sub(
                r'_m_h5_tk=[^;]+',
                f'_m_h5_tk={self.m_h5_tk}',
                self.cookie_str
            )
            logger.info(
                "_ensure_cookie_has_mh5_tk: accountId=%d, 已修复 cookie credential",
                self.account_id,
            )

        if not cookie_m_h5_tk and self.m_h5_tk:
            # cookie 中不含 _m_h5_tk，需要追加进去
            logger.warning(
                "_ensure_cookie_has_mh5tk: accountId=%d, cookie 中缺少 _m_h5_tk, "
                "将生成 accessToken 所用的值追加到 cookie",
                self.account_id,
            )
            if self.cookie_str:
                self.cookie_str = f"_m_h5_tk={self.m_h5_tk}; {self.cookie_str}"
            else:
                self.cookie_str = f"_m_h5_tk={self.m_h5_tk}"
            logger.info(
                "_ensure_cookie_has_mh5tk: accountId=%d, 已追加 credential 到 cookie",
                self.account_id,
            )

    async def _connect(self):
        """建立 WebSocket 连接并初始化。"""
        # Step 1: 获取 accessToken
        self.phase = "refresh_token"
        if not await self._refresh_token():
            self.last_error = "获取 WebSocket Token 失败，Cookie/_m_h5_tk 可能已过期或触发滑块验证"
            self.phase = "token_failed"
            logger.error("获取 WebSocket Token 失败 accountId=%d", self.account_id)
            await asyncio.sleep(RECONNECT_DELAY)
            return

        # === 关键：确保 cookie 中的 _m_h5_tk 与 accessToken 匹配 ===
        # 必须在 _refresh_token 之后（此时 self.m_h5_tk 已是最新的有效值）
        self._ensure_cookie_has_mh5tk()

        cookie_m_h5_tk = extract_m_h5_tk_from_cookie(self.cookie_str)

        logger.info(
            "获取 accessToken 成功 accountId=%d credential_present=%s credential_match=%s",
            self.account_id,
            bool(self._access_token),
            bool(self.m_h5_tk and cookie_m_h5_tk and cookie_m_h5_tk == self.m_h5_tk),
        )
        if self.m_h5_tk and cookie_m_h5_tk:
            logger.info(
                "cookie _m_h5_tk 一致性检查: accountId=%d, 匹配=%s",
                self.account_id,
                "是" if cookie_m_h5_tk == self.m_h5_tk else "否",
            )
        elif not cookie_m_h5_tk:
            logger.error(
                "cookie _m_h5_tk 一致性检查: accountId=%d, cookie 中仍然缺少 _m_h5_tk! "
                "WebSocket 连接很可能被服务端静默丢弃",
                self.account_id,
            )

        # Step 2: 建立 WSS 连接（不添加 ?token= 参数，token 只在 /reg 消息中传递）
        ws_url = WS_URL
        additional_headers = {
            "Cookie": self.cookie_str,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.7680.177 Safari/537.36"
            ),
            "Origin": "https://www.goofish.com",
            "Referer": "https://www.goofish.com/",
            "Host": "wss-goofish.dingtalk.com",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
        }

        ws = _ThreadedWebSocketAdapter(
            account_id=self.account_id,
            url=ws_url,
            headers=additional_headers,
        )

        try:
            self.phase = "connecting"
            logger.info("连接 WS accountId=%d 使用 websocket-client 底层实现", self.account_id)
            await ws.connect(timeout=10)
            self._ws = ws
            # 对标商业版 im_client.py:148：WS 连接建立后立即标记 _connected
            self._connected = True
            self.phase = "connected_socket"
            logger.info("WS 已连接 accountId=%d", self.account_id)

            # Step 3: 注册
            self.phase = "registering"
            if not await self._do_reg(ws):
                self.last_error = "WebSocket 已连接但注册失败，可能是 accessToken/deviceId/Cookie 不匹配"
                self.phase = "register_failed"
                self._connected = False
                return
            # 对标商业版 im_client.py:156：注册成功后标记 _registered
            self._registered = True

            # Step 4: 同步改为后台任务，不阻塞连接成功
            # 商业版 im_client.py:155-163：注册后立即 return True，同步/心跳在后台进行
            self.phase = "connected"
            self.last_error = ""
            logger.info("WS 连接成功（已注册）accountId=%d", self.account_id)

            # 后台发送同步消息（不阻塞连接状态）
            async def _bg_sync():
                try:
                    await self._do_sync(ws)
                except Exception as exc:
                    logger.warning("后台同步发送失败 accountId=%d errorType=%s", self.account_id, type(exc).__name__)

            asyncio.create_task(_bg_sync())

            # Step 5: 消息接收循环 + 心跳
            await self._message_loop(ws)

        except ConnectionError as exc:
            self.last_error = "WebSocket 连接关闭"
            self.phase = "closed"
            logger.warning(
                "WS 连接关闭 accountId=%d errorType=%s",
                self.account_id,
                type(exc).__name__,
            )
        except Exception as exc:
            self.last_error = f"WebSocket 连接异常（{type(exc).__name__}）"
            self.phase = "error"
            logger.error(
                "WS 连接异常 accountId=%d errorType=%s",
                self.account_id,
                type(exc).__name__,
            )
        finally:
            self._connected = False
            self._registered = False
            self._sid = None
            self._ws = None
            try:
                await ws.close()
            except Exception:
                pass

    async def _update_token_in_db(self, new_m_h5_tk: str):
        """将刷新后的 _m_h5_tk 写回数据库。"""
        try:
            from ..core.database import async_session
            from sqlalchemy import text
            async with async_session() as db:
                await db.execute(
                    text(
                        "UPDATE xianyu_account_auth SET encrypted_token = :tk, updated_time = NOW() "
                        "WHERE account_id = :aid"
                    ),
                    {"tk": encrypt_cookie_for_storage(new_m_h5_tk), "aid": self.account_id},
                )
                await db.commit()
            logger.info(
                "已更新 DB 中的 _m_h5_tk: accountId=%d credential_present=%s",
                self.account_id,
                bool(new_m_h5_tk),
            )
        except Exception as exc:
            logger.warning(
                "更新 DB 中的 _m_h5_tk 失败（不影响连接）errorType=%s",
                type(exc).__name__,
            )

    async def _update_cookie_in_db(self, new_cookie_str: str):
        """将刷新后的 cookie 字符串写回数据库。"""
        try:
            from ..core.database import async_session
            from sqlalchemy import text
            async with async_session() as db:
                await db.execute(
                    text(
                        "UPDATE xianyu_account_auth SET encrypted_cookie = :cookie, updated_time = NOW() "
                        "WHERE account_id = :aid"
                    ),
                    {"cookie": encrypt_cookie_for_storage(new_cookie_str), "aid": self.account_id},
                )
                await db.commit()
            logger.info(
                "已更新 DB 中的 cookie: accountId=%d credential_present=%s",
                self.account_id,
                bool(new_cookie_str),
            )
        except Exception as exc:
            logger.warning(
                "更新 DB 中的 cookie 失败（不影响连接）errorType=%s",
                type(exc).__name__,
            )

    async def _update_cookie_status(self, status: int, code: str | None = None, message: str | None = None):
        """更新 cookie_status（如遇到滑块验证时标记为失效）。"""
        try:
            from ..core.database import async_session
            from sqlalchemy import text
            async with async_session() as db:
                await db.execute(
                    text(
                        "UPDATE xianyu_account_auth SET cookie_status = :st, "
                        "last_login_status_code = :code, last_login_status_message = :message, "
                        "last_login_check_time = NOW(), updated_time = NOW() "
                        "WHERE account_id = :aid"
                    ),
                    {
                        "st": status,
                        "code": code,
                        "message": message,
                        "aid": self.account_id,
                        },
                )
                await db.execute(
                    text(
                        "UPDATE xianyu_account_runtime SET cookie_status = :st, "
                        "last_login_status_code = :code, last_login_status_message = :message, "
                        "last_login_check_time = NOW(), updated_time = NOW() "
                        "WHERE account_id = :aid"
                    ),
                    {
                        "st": status,
                        "code": code,
                        "message": message,
                        "aid": self.account_id,
                        },
                )
                await db.commit()
            logger.info(
                "已更新 cookie_status=%d: accountId=%d", status, self.account_id
            )
            # SSE 广播 cookie 状态变更，前端实时更新
            try:
                await broadcaster.broadcast("cookie_status_changed", {
                    "accountId": self.account_id,
                    "cookieStatus": status,
                })
                logger.info(
                    "SSE 已广播 cookie_status_changed: accountId=%d, status=%d",
                    self.account_id, status
                )
            except Exception as sse_err:
                logger.warning(
                    "SSE 广播 cookie_status 失败 errorType=%s",
                    type(sse_err).__name__,
                )
            # Cookie 失效时推送飞书机器人通知（失败不影响主流程）
            if status == 0:
                try:
                    from .notify_dispatcher import notify_cookie_expired
                    await notify_cookie_expired(self.account_id, status)
                except Exception as exc:
                    logger.debug(
                        "Cookie 失效飞书通知异常，忽略 errorType=%s",
                        type(exc).__name__,
                    )
            elif status == 1:
                try:
                    from .notify_dispatcher import clear_cookie_expired_state
                    await clear_cookie_expired_state(self.account_id)
                except Exception as exc:
                    logger.debug(
                        "Cookie 恢复通知状态清理异常，忽略 errorType=%s",
                        type(exc).__name__,
                    )
        except Exception as exc:
            logger.warning(
                "更新 cookie_status 失败 errorType=%s",
                type(exc).__name__,
            )

    async def _refresh_token(self) -> bool:
        """获取/刷新 accessToken。如果 DB 中的 _m_h5_tk 过期，自动从 cookie 提取并更新 DB。
        如果 cookie 中的 _m_h5_tk 也已过期，尝试调用 refresh_m_h5_tk 刷新令牌。
        遇到滑块验证时自动更新 cookie_status 为 0（失效）。
        """
        access_token, effective_m_h5_tk, error_type, refreshed_cookie = get_ws_token_with_refreshed_m_h5_tk(
            self.cookie_str, self.m_h5_tk
        )
        if access_token:
            self._access_token = access_token
            # 如果用了刷新后的 token，更新 DB 中的 token 和 cookie
            if effective_m_h5_tk and effective_m_h5_tk != self.m_h5_tk:
                self.m_h5_tk = effective_m_h5_tk
                await self._update_token_in_db(effective_m_h5_tk)
                logger.info(
                    "WS Token 获取: accountId=%d 使用刷新后的 credential，已更新 DB",
                    self.account_id,
                )
            else:
                logger.info(
                    "WS Token 获取: accountId=%d 使用现有 credential",
                    self.account_id,
                )
            # 如果 cookie 被刷新了（含新的 _m_h5_tk），更新 DB 中的 cookie
            if refreshed_cookie and refreshed_cookie != self.cookie_str:
                self.cookie_str = refreshed_cookie
                await self._update_cookie_in_db(refreshed_cookie)
            return True

        # 滑块验证：不立即标记 cookie_status=0
        # 滑块验证可能是暂时的（如请求头缺失导致的风控误判），_connect_loop 会在
        # RECONNECT_DELAY 秒后自动重试。仅当 Cookie 真正过期（error_type=expired）
        # 时才标记失效，避免扫码后立即看到"Cookie 已失效"的误报。
        if error_type == "captcha":
            logger.warning(
                "WS Token 获取遇到滑块验证，暂不标记 cookie_status=0，等待重连重试: accountId=%d",
                self.account_id
            )
            # 清除 Token 缓存，下次重连重新获取
            if self.unb:
                invalidate_cached_token(self.unb)
            try:
                from .notify_dispatcher import notify_captcha_required
                await notify_captcha_required(self.account_id, "WS Token 获取触发滑块验证")
            except Exception as exc:
                logger.debug(
                    "人机验证通知异常，忽略 errorType=%s",
                    type(exc).__name__,
                )
            # 自动触发滑块求解（失败不影响主流程，状态由求解器内部决定）
            await self._auto_solve_captcha_after_failure(scene="captcha")
        elif error_type == "expired":
            logger.warning(
                "WS Token 获取失败（Cookie 已过期）: accountId=%d",
                self.account_id
            )
            # 清除 Token 缓存，Cookie 已过期缓存也无效
            if self.unb:
                invalidate_cached_token(self.unb)
            await self._update_cookie_status(0, "COOKIE_EXPIRED", "Cookie 已过期，请重新登录闲鱼账号")
            # 自动触发滑块求解：Session 过期场景下闲鱼可能要求重新过滑块，
            # 求解器内部会通过 Token API 二次验证判断 Cookie 是否真的可用，
            # 若 Token API 仍返回 expired 则保持 cookie_status=0 让用户重新登录
            await self._auto_solve_captcha_after_failure(scene="expired")

        return False

    async def _auto_solve_captcha_after_failure(self, scene: str = "captcha") -> None:
        """WS Token 获取失败后自动触发滑块求解。

        调用 handle_captcha_for_account(autoSolve=True)，内部会：
        1. 启动 Playwright 检测/求解滑块
        2. 通过 Token API 二次验证 Cookie 是否真实可用
        3. 可用时恢复 cookie_status=1 并触发 _m_h5_tk 刷新
        4. 不可用（Session 真过期）时保持 cookie_status=0，通知用户重新登录

        失败不影响主流程，仅记录日志。同账号 10 分钟内只自动求解一次，
        避免断线重连循环反复启动浏览器。

        Args:
            scene: 触发场景，"captcha" 表示滑块验证，"expired" 表示 Session 过期，
                   "heartbeat_stop" 表示心跳停跳触发的主动求解
        """
        # === 去重：同账号 10 分钟内只自动求解一次（跨模块共享） ===
        if not _should_auto_solve(self.account_id):
            logger.info(
                "账号 %d 自动滑块求解去重跳过（10 分钟内已执行过）scene=%s",
                self.account_id, scene,
            )
            return
        _mark_auto_solve_started(self.account_id)

        logger.info(
            "WS Token 失败后自动触发滑块求解 accountId=%d scene=%s",
            self.account_id, scene,
        )
        try:
            from .captcha_solver import handle_captcha_for_account
            # 关键：必须传非 manual 的 trigger_scene（而非默认的 "manual"）。
            # handle_captcha_for_account 内部对 manual/manual_retry 场景会跳过
            # Token API 二次验证（因为手动场景下用户刚扫码，Cookie 是新鲜的，
            # 但 _m_h5_tk 可能尚未生成，Token API 会误判过期）。
            # 而 WS 自动触发场景下 Cookie 是已有的、刚刚撞过滑块的，必须通过
            # Token API 二次验证确认 Cookie 真实可用，才能正确持久化 WS 状态
            # （cookie_status / login_status_code），避免 Session 真过期时
            # 错误恢复 cookie_status=1 导致 WS 反复重连失败。
            # heartbeat_stop 使用独立 trigger_scene 以便求解记录区分来源。
            trigger_scene = "heartbeat_stop" if scene == "heartbeat_stop" else "ws_connect"
            result = await handle_captcha_for_account(
                account_id=self.account_id, response=None,
                auto_solve=True,
                trigger_scene=trigger_scene,
                open_reason=f"WS Token 获取失败（scene={scene}）自动触发滑块求解",
                solve_reason=f"WS 自动重连流程触发（{scene}）",
            )
            recovered = bool(result.get("recovered"))
            auto_solve_result = result.get("autoSolveResult") or {}
            cookie_verified = auto_solve_result.get("cookieVerified", True)

            if recovered:
                logger.info(
                    "自动滑块求解成功，Cookie 已恢复 accountId=%d scene=%s",
                    self.account_id, scene,
                )
                # 求解成功后立即尝试重新连接 WS（不等下一次重连周期）
                try:
                    spawn_background_task(
                        ws_manager.restart_account(self.account_id),
                        name=f"ws.restart-account:{self.account_id}",
                    )
                    logger.info("自动滑块求解后已触发 WS 重连 accountId=%d", self.account_id)
                except Exception as exc:
                    logger.warning(
                        "自动滑块求解后触发 WS 重连失败 accountId=%d errorType=%s",
                        self.account_id,
                        type(exc).__name__,
                    )
            elif auto_solve_result.get("solved") and not cookie_verified:
                # 滑块通过但 Cookie Session 真过期
                logger.warning(
                    "自动滑块求解通过但 Cookie Session 已过期，需要用户重新扫码登录 accountId=%d",
                    self.account_id,
                )
                # 通知用户需要重新登录（带明确文案，引导用户去账号管理页）
                try:
                    from .notify_dispatcher import dispatch_notification
                    await dispatch_notification(
                        event_display_name="Cookie 到期",
                        title="账号 Cookie Session 已过期",
                        content=(
                            f"账号名称：{await _lookup_account_name_safe(self.account_id)}\n"
                            f"状态：Cookie Session 已过期（自动滑块已通过但 Token API 仍拒绝）\n"
                            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"请前往账号管理页或连接管理页重新扫码登录闲鱼账号获取新 Cookie。"
                        ),
                    )
                except Exception as exc:
                    logger.debug(
                        "Session 过期通知异常，忽略 errorType=%s",
                        type(exc).__name__,
                    )

                # === 通过飞书自建应用推送 Session 过期通知 ===
                # 飞书只提供状态通知和 Web 账号管理页引导，不执行二维码自动登录。
                try:
                    from .feishu_chat import notify_session_expired_via_feishu_app
                    account_name = await _lookup_account_name_safe(self.account_id)
                    spawn_background_task(
                        notify_session_expired_via_feishu_app(
                            account_id=self.account_id,
                            account_name=account_name,
                        ),
                        name=f"ws.feishu-session-expired:{self.account_id}",
                    )
                    logger.info("已触发飞书自建应用通知 Session 过期 accountId=%d", self.account_id)
                except Exception as exc:
                    logger.debug(
                        "飞书自建应用通知异常，忽略 errorType=%s",
                        type(exc).__name__,
                    )
            else:
                logger.warning(
                    "自动滑块求解失败 accountId=%d scene=%s failureReasonPresent=%s",
                    self.account_id,
                    scene,
                    bool(auto_solve_result.get("error")),
                )
        except Exception as exc:
            logger.error(
                "自动滑块求解异常 accountId=%d scene=%s errorType=%s",
                self.account_id,
                scene,
                type(exc).__name__,
            )

        return False

    async def _do_reg(self, ws: _ThreadedWebSocketAdapter) -> bool:
        """发送 /reg 注册消息并等待响应。使用循环接收，捕获服务端所有回包。"""
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.177 Safari/537.36 "
            "DingTalk(2.1.5) OS(Windows/10) Browser(Chrome/146.0.7680.177) "
            "DingWeb/2.1.5 IMPaaS DingWeb/2.1.5"
        )

        device_id = self.device_id

        # === deviceId 一致性检查 ===
        if not device_id:
            logger.error(
                "WS 注册无法发送: deviceId 为空 accountId=%d credential_present=%s",
                self.account_id,
                bool(self.m_h5_tk),
            )
            return False

        logger.info(
            "WS 注册 did 检查: accountId=%d did_present=%s credential_present=%s",
            self.account_id,
            bool(device_id),
            bool(self.m_h5_tk),
        )

        msg = build_reg_message(self._access_token, device_id, ua=ua)

        logger.info(
            "WS 注册消息已构建: accountId=%d credential_present=%s",
            self.account_id,
            bool(self._access_token),
        )

        # 商业版在 WS 连接建立后立即发送 /reg，无延迟。移除 sleep(0.5) 加快连接速度。
        logger.info("WS 注册消息发送中: accountId=%d", self.account_id)

        await ws.send(json.dumps(msg, ensure_ascii=False))
        logger.info("WS 注册消息已发送: accountId=%d, 开始等待响应(10秒超时)", self.account_id)

        # === 循环接收：捕获 10 秒内服务端发来的所有消息 ===
        deadline = time.time() + 10
        received_count = 0
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
                received_count += 1
                data = json.loads(resp)
                lwp = data.get("lwp", "")

                logger.info(
                    "WS 注册等待中收到协议响应 accountId=%d code=%s",
                    self.account_id,
                    data.get("code"),
                )

                # 检查 401 / token 失效
                if data.get("code") == 401:
                    logger.error("WS 注册失败: token 无效/过期 accountId=%d code=401", self.account_id)
                    return False

                # 注册成功响应有两种形态：
                # 1. lwp == /reg 且 code == 200
                # 2. lwp 为空，但 headers 中直接返回 reg-sid / sid
                if data.get("code") == 200:
                    headers = data.get("headers", {})
                    reg_sid = headers.get("reg-sid") or headers.get("sid")
                    if reg_sid:
                        self._sid = reg_sid
                        logger.info("WS 注册成功 accountId=%d", self.account_id)
                        return True

                # 非注册消息：继续等待
                logger.info("WS 注册等待中忽略非注册消息 accountId=%d", self.account_id)

            except asyncio.TimeoutError:
                continue
            except json.JSONDecodeError:
                logger.warning(
                    "WS 注册等待中收到非 JSON 消息 accountId=%d payloadLength=%d",
                    self.account_id,
                    len(resp),
                )
                continue

        if not received_count:
            logger.error(
                "WS 注册超时 accountId=%d (10秒内服务端未发送任何消息)",
                self.account_id,
            )
        else:
            logger.error(
                "WS 注册超时 accountId=%d (10秒内共收到 %d 条消息，但未收到 /reg 成功响应)",
                self.account_id, received_count,
            )

        return False

    async def _do_sync(self, ws: _ThreadedWebSocketAdapter):
        """发送 /r/SyncStatus/ackDiff 同步消息。"""
        if self._sync_pts <= 0:
            self._sync_pts = int(time.time() * 1000000)  # 微秒
        msg = build_sync_message(self._sync_pts, self._sync_high_pts)
        await ws.send(json.dumps(msg, ensure_ascii=False))
        logger.info(
            "WS 同步请求已发送 accountId=%d pts=%s highPts=%s",
            self.account_id, self._sync_pts, self._sync_high_pts
        )

    async def _message_loop(self, ws: _ThreadedWebSocketAdapter):
        """消息接收循环，包含心跳。"""
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

        try:
            while True:
                raw_msg = await ws.recv()
                try:
                    await self._handle_message(raw_msg, ws)
                except Exception as exc:
                    logger.error(
                        "处理 WS 消息异常 accountId=%d errorType=%s",
                        self.account_id,
                        type(exc).__name__,
                    )
        except ConnectionError:
            logger.warning("WS 接收循环连接关闭 accountId=%d", self.account_id)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, ws: _ThreadedWebSocketAdapter):
        """心跳保活循环（15秒间隔）。"""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                # 如果超过 45 秒未收到服务端任何消息，很可能连接已静默断开
                idle_secs = time.time() - self._last_recv_time
                if idle_secs > HEARTBEAT_INTERVAL * 3:
                    logger.warning(
                        "WS 连接已静默断开 accountId=%d (%.0f秒无消息), "
                        "主动关闭连接以触发重连",
                        self.account_id, idle_secs,
                    )
                    # 如果用户启用了"心跳停跳"触发场景，在关闭连接前
                    # 后台发起自动滑块求解（不阻塞重连流程）。
                    # 求解器内部会通过 Token API 二次验证判断是否真需要求解，
                    # 若 Token API 正常则不会实际发起求解。
                    try:
                        from .remote_slider_config import (
                            TRIGGER_SCENE_HEARTBEAT_STOP,
                            is_trigger_scene_enabled,
                        )
                        if await is_trigger_scene_enabled(TRIGGER_SCENE_HEARTBEAT_STOP):
                            logger.info(
                                "心跳停跳触发自动滑块求解 accountId=%d idle=%.0fs",
                                self.account_id, idle_secs,
                            )
                            spawn_background_task(
                                self._auto_solve_captcha_after_failure(scene="heartbeat_stop"),
                                name=f"ws.heartbeat-auto-solve:{self.account_id}",
                            )
                    except Exception as exc:
                        logger.debug(
                            "心跳停跳触发场景检查异常 errorType=%s",
                            type(exc).__name__,
                        )
                    # 必须关闭 WS，否则 _message_loop 会阻塞在 ws.recv() 上，
                    # _connect 的 finally 块不会执行，_connected 保持 True，
                    # 后续消息会发送到死连接上导致超时。
                    await ws.close()
                    break

                msg = build_heartbeat_message()
                await ws.send(json.dumps(msg, ensure_ascii=False))
                logger.debug("WS 心跳发送 accountId=%d", self.account_id)
            except Exception as exc:
                logger.warning(
                    "WS 心跳异常 accountId=%d errorType=%s",
                    self.account_id,
                    type(exc).__name__,
                )
                # 同样需要关闭 WS 以确保状态正确回收
                try:
                    await ws.close()
                except Exception:
                    pass
                break

    async def _handle_message(self, raw_msg: str, ws: _ThreadedWebSocketAdapter):
        """处理收到的 WebSocket 消息。"""
        self._last_recv_time = time.time()
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.warning("WS 消息 JSON 解析失败 payloadLength=%d", len(raw_msg))
            return

        lwp = data.get("lwp", "")

        # === ACK / IM 数据响应处理（必须在 lwp 分发之前）===
        # 闲鱼 IM 响应有两种形态：
        #   1. 纯 ACK（发送消息后）：{"code": 200, "headers": {"mid": "...", "sid": "..."}}
        #   2. IM 数据响应（list_conversations / list_messages 等）：
        #      {"code": 200, "headers": {"mid": "..."}, "body": {userConvs: [...], hasMore: ...}}
        # 对标商业版 im_client.py:984-1005：匹配 mid 后直接 resolve 完整 message（含 body），
        # 否则 list_conversations 的响应 body（含 userConvs）会被丢弃，导致会话列表为空。
        code = data.get("code")
        headers = data.get("headers", {})
        if isinstance(headers, dict):
            mid = headers.get("mid", "")
            if mid and mid in self._send_futures:
                fut = self._send_futures.pop(mid)
                if not fut.done():
                    final_code = code if code else 200
                    body = data.get("body", {})
                    has_body = isinstance(body, dict) and bool(body)

                    if final_code == 200 or has_body:
                        # 成功响应或含 body 的 IM 数据响应：返回完整 message（含 body）
                        # 对标商业版 im_client.py:998: future.set_result(message)
                        logger.info(
                            "WS 收到响应 accountId=%d code=%s hasBody=%s bodyKeys=%s",
                            self.account_id,
                            final_code,
                            has_body,
                            list(body.keys()) if isinstance(body, dict) else None,
                        )
                        fut.set_result(data)
                    else:
                        # 失败 ACK（无 body 或 body 非 dict）
                        result: dict[str, Any] = {
                            "code": final_code,
                            "mid": mid,
                            "uuid": mid,
                        }
                        if isinstance(body, dict):
                            error_msg = (
                                body.get("message")
                                or body.get("msg")
                                or body.get("error")
                                or body.get("reason")
                                or body.get("developerMessage")
                                or ""
                            )
                            normalized_error = str(error_msg or "").lower()
                            if "conversation not exist" in normalized_error:
                                result["errorKind"] = "conversation_missing"
                            result["error"] = "平台拒绝发送请求"
                        else:
                            result["error"] = "平台拒绝发送请求"
                        logger.warning(
                            "WS 收到失败响应 accountId=%d code=%s error=%s",
                            self.account_id,
                            final_code,
                            result.get("error"),
                        )
                        fut.set_result(result)
                return

        if lwp in ("/s/para", "/s/sync", "/s/vulcan", "/r/SyncStatus/ackDiff"):
            # 同步包（聊天消息/增量推送）
            await self._handle_sync_package(data, ws)
        elif lwp == "/r/MessageSend/sendByReceiverScope":
            # 发送消息的响应
            await self._handle_send_response(data)
        elif lwp == "/!":
            # 心跳响应（此时 mid 不在 _send_futures 中，是真正的心跳）
            pass
        elif lwp == "/reg":
            # 注册响应（已在 _do_reg 处理）
            pass
        else:
            logger.warning(
                "WS 未处理的消息类型 accountId=%d topLevelFieldCount=%d",
                self.account_id,
                len(data),
            )

    async def _handle_sync_package(self, data: dict, ws: _ThreadedWebSocketAdapter):
        """处理同步包（聊天消息）。"""
        lwp = data.get("lwp", "")
        is_ack_diff = lwp == "/r/SyncStatus/ackDiff"

        # 提取同步响应中的高点水印
        if is_ack_diff:
            try:
                body = data.get("body", {})
                if isinstance(body, dict):
                    sync_push = body.get("syncPushPackage") or body
                    max_high_pts = sync_push.get("maxHighPts", 0)
                    max_pts = sync_push.get("maxPts", 0)
                    if max_high_pts:
                        self._sync_high_pts = max_high_pts
                    if max_pts:
                        self._sync_pts = max_pts
                    logger.info(
                        "WS ackDiff 响应 accountId=%d maxHighPts=%s maxPts=%s",
                        self.account_id, max_high_pts, max_pts
                    )
            except Exception as exc:
                logger.warning(
                    "WS ackDiff 响应解析异常 accountId=%d errorType=%s",
                    self.account_id,
                    type(exc).__name__,
                )

        result = parse_sync_package(data)
        if not result:
            # ackDiff 响应即使没有消息也要继续下一轮同步（长轮询）
            if is_ack_diff:
                await self._do_sync(ws)
            return

        messages = result.get("messages", [])
        ack_ids = result.get("ack_pnm_ids", [])

        if not messages:
            logger.info("WS 同步包未解析出可入库消息 accountId=%d", self.account_id)
            # ackDiff 响应即使没有消息也要继续下一轮同步（长轮询）
            if is_ack_diff:
                await self._do_sync(ws)
            return

        logger.info("WS 收到 %d 条消息 accountId=%d", len(messages), self.account_id)

        # 1. 回复 ACK
        for pnm_id in ack_ids:
            try:
                ack_msg = build_ack_message(pnm_id)
                await ws.send(json.dumps(ack_msg, ensure_ascii=False))
            except Exception as exc:
                logger.error(
                    "WS ACK 发送失败 accountId=%d errorType=%s",
                    self.account_id,
                    type(exc).__name__,
                )

        # 2. 先发起下一轮 ackDiff，避免当前批次的消息处理阻塞后续同步。
        if is_ack_diff:
            await self._do_sync(ws)

        # 3. 处理当前批次消息（落库 + SSE 广播）。
        for msg in messages:
            await self._process_message(msg)

    async def _process_message(self, msg: dict):
        """处理单条消息（保存 + 回调 + 广播）。"""
        s_id = msg.get("sId", "")
        pnm_id = msg.get("pnmId", "")
        msg_content = msg.get("msgContent", "")
        sender_id = msg.get("senderUserId", "")
        logger.info(
            "WS 处理消息 accountId=%d hasSession=%s hasMessageId=%s hasSender=%s contentLen=%d contentType=%s",
            self.account_id, bool(s_id), bool(pnm_id), bool(sender_id),
            len(str(msg_content)) if msg_content else 0, msg.get("contentType", "?")
        )
        if not msg_content and (s_id in ("", "1") or pnm_id in ("", "1")):
            logger.warning(
                "WS 消息字段不完整 accountId=%d keys=%s",
                self.account_id,
                sorted(str(key) for key in msg.keys())[:30],
            )
        # 1. 外部回调（如数据库保存）
        if self.on_message_callback:
            try:
                await self.on_message_callback(self.account_id, msg)
            except Exception as exc:
                logger.error(
                    "消息回调失败 accountId=%d errorType=%s",
                    self.account_id,
                    type(exc).__name__,
                )

        # 2. SSE 广播到前端
        try:
            direction = str(msg.get("direction") or "IN").upper()
            sender_id = str(msg.get("senderUserId") or "")
            receiver_id = str(msg.get("receiverUserId") or "")
            peer_user_id = receiver_id if direction == "OUT" and receiver_id else sender_id
            if not peer_user_id and msg.get("sId"):
                peer_user_id = f"sid:{msg.get('sId')}"
            # 使用 normalize_peer_name 过滤系统文本
            raw_user_name = "" if direction == "OUT" else str(msg.get("senderUserName") or "")
            peer_user_name = normalize_peer_name(raw_user_name)
            if not peer_user_name and peer_user_id.startswith("sid:"):
                peer_user_name = "闲鱼买家"
            await broadcaster.broadcast("message", {
                "accountId": self.account_id,
                "sId": msg.get("sId", ""),
                "sid": msg.get("sId", ""),
                "pnmId": msg.get("pnmId", ""),
                "senderUserId": sender_id,
                "senderUserName": msg.get("senderUserName", ""),
                "receiverUserId": receiver_id,
                "peerUserId": peer_user_id,
                "peerUserName": peer_user_name,
                "peerNick": peer_user_name,
                "msgContent": msg.get("msgContent", ""),
                "message": msg.get("msgContent", ""),
                "content": msg.get("msgContent", ""),
                "contentType": msg.get("contentType", 1),
                "messageTime": msg.get("messageTime", 0),
                "direction": msg.get("direction", ""),
                "reminderContent": msg.get("reminderContent", ""),
                "reminderUrl": msg.get("reminderUrl", ""),
                "xyGoodsId": msg.get("xyGoodsId", ""),
                "readStatus": 1 if direction == "OUT" else 0,
            })
        except Exception as exc:
            logger.error(
                "SSE 广播失败 accountId=%d errorType=%s",
                self.account_id,
                type(exc).__name__,
            )

    async def _handle_send_response(self, data: dict):
        """处理发送消息的响应。"""
        result = parse_send_response(data)
        if not result:
            return

        # 先尝试 uuid 匹配（兼容有 lwp 且带 uuid 的完整响应）
        uuid_val = result.get("uuid")
        if uuid_val and uuid_val in self._send_futures:
            fut = self._send_futures.pop(uuid_val)
            if not fut.done():
                fut.set_result(result)
            return

        # 再尝试 mid 匹配兜底（某些响应可能带 lwp 但仍以 mid 作为标识）
        headers = data.get("headers", {})
        mid = headers.get("mid", "") if isinstance(headers, dict) else ""
        if mid and mid in self._send_futures:
            fut = self._send_futures.pop(mid)
            if not fut.done():
                fut.set_result(result)


class WebSocketManager:
    """WebSocket 连接管理器。

    管理多个账号的 WebSocket 客户端实例。
    """

    def __init__(self):
        self._clients: dict[int, XianyuWebSocketClient] = {}
        self._on_message_callback: Optional[Callable] = None

    def set_message_callback(self, callback: Callable):
        """设置消息回调函数。"""
        self._on_message_callback = callback

    async def start_client(
        self,
        account_id: int,
        cookie_str: str,
        m_h5_tk: str,
        unb: str,
    ) -> bool:
        """启动指定账号的 WebSocket 客户端。"""
        # 停止已有客户端
        await self.stop_client(account_id)

        client = XianyuWebSocketClient(
            account_id=account_id,
            cookie_str=cookie_str,
            m_h5_tk=m_h5_tk,
            unb=unb,
            on_message_callback=self._on_message_callback,
        )
        self._clients[account_id] = client

        await client.start()
        logger.info("WS 管理器: 启动客户端 accountId=%d", account_id)
        return True

    async def stop_client(self, account_id: int):
        """停止指定账号的 WebSocket 客户端。"""
        client = self._clients.pop(account_id, None)
        if client:
            await client.stop()
            logger.info("WS 管理器: 停止客户端 accountId=%d", account_id)

    async def stop_all(self):
        """停止所有 WebSocket 客户端。"""
        for account_id in list(self._clients.keys()):
            await self.stop_client(account_id)

    def get_client(self, account_id: int) -> Optional[XianyuWebSocketClient]:
        """获取指定账号的 WebSocket 客户端。"""
        return self._clients.get(account_id)

    def get_status(self, account_id: int) -> dict:
        """获取指定账号的连接状态。"""
        client = self._clients.get(account_id)
        if client:
            return {
                "connected": client.is_connected,
                "hasSid": client._sid is not None,
                "deviceIdPresent": bool(client.device_id),
                "tokenCredentialPresent": bool(client.m_h5_tk),
                "phase": getattr(client, "phase", "unknown"),
                "lastError": getattr(client, "last_error", ""),
            }
        return {
            "connected": False,
            "hasSid": False,
            "deviceIdPresent": False,
            "tokenCredentialPresent": False,
            "phase": "not_started",
            "lastError": "",
        }

    async def restart_account(self, account_id: int) -> bool:
        """重启指定账号的 WebSocket 客户端（自动从 DB 读取最新 Cookie/Token）。

        供自动滑块求解成功后调用，让 WS 立即重连而不等下一次重连周期。

        Returns:
            True 表示已触发重启，False 表示账号未找到或启动失败
        """
        try:
            from ..core.database import async_session
            from sqlalchemy import text
            from .cookie_token_refresher import _load_active_accounts  # noqa: F401

            # 从 DB 读取最新账号信息
            async with async_session() as db:
                row = (await db.execute(
                    text(
                        "SELECT a.id AS account_id, a.external_uid AS unb, "
                        "auth.encrypted_cookie, auth.encrypted_token "
                        "FROM xianyu_account a "
                        "JOIN xianyu_account_auth auth "
                        "  ON auth.account_id = a.id "
                        "WHERE a.id = :aid AND a.deleted = 0 "
                        "AND COALESCE(auth.deleted, 0) = 0 LIMIT 1"
                    ),
                    {"aid": account_id},
                )).mappings().first()
            if not row:
                logger.warning("restart_account: 账号不存在 accountId=%d", account_id)
                return False

            from ..core.cookie_crypto import decrypt_cookie_if_needed
            cookie_str = decrypt_cookie_if_needed(row["encrypted_cookie"])
            m_h5_tk = decrypt_cookie_if_needed(row["encrypted_token"]) if row["encrypted_token"] else None
            unb = str(row["unb"] or "")

            if not cookie_str or not m_h5_tk:
                logger.warning(
                    "restart_account: Cookie 或 _m_h5_tk 为空，无法重启 accountId=%d",
                    account_id,
                )
                return False

            await self.start_client(
                account_id=account_id,
                cookie_str=cookie_str,
                m_h5_tk=m_h5_tk,
                unb=unb,
            )
            logger.info("restart_account: 已触发重启 accountId=%d", account_id)
            return True
        except Exception as exc:
            logger.error(
                "restart_account 异常 accountId=%d errorType=%s",
                account_id,
                type(exc).__name__,
            )
            return False


# 全局 WebSocket 管理器实例
ws_manager = WebSocketManager()
