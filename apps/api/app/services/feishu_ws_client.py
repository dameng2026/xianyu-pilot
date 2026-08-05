"""
飞书长连接（WebSocket）事件客户端
================================

通过 lark_oapi 的 ws.Client 与飞书开放平台建立 WebSocket 全双工通道，
接收事件订阅（长连接模式），事件数据为明文、已内建鉴权。

长连接模式无需公网 URL / 内网穿透，本地即可接收飞书消息事件。

支持的事件：
- im.message.receive_v1 → 复用 HTTP webhook 的消息处理链路
  （parse_message_event + handle_feishu_user_message + send_text_message）

配置来源：user_notification_setting.config_json.channels[] 中
type='feishu_app' 且 enabled=True 的元素（appId / appSecret）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client: Any = None
_started = False
_stop_event: Optional[asyncio.Event] = None


async def _load_app_credentials() -> Optional[dict]:
    """从数据库中读取飞书自建应用凭证（appId / appSecret）。"""
    try:
        from .feishu_bot import _load_feishu_app_config

        config = await _load_feishu_app_config()
        if not config:
            return None
        app_id = str(config.get("appId") or "").strip()
        app_secret = str(config.get("appSecret") or "").strip()
        if not app_id or not app_secret:
            logger.warning("飞书长连接：应用凭证不完整（appId/appSecret 为空）")
            return None
        return {"appId": app_id, "appSecret": app_secret}
    except Exception as exc:
        logger.warning(
            "飞书长连接：加载应用凭证异常 errorType=%s",
            type(exc).__name__,
        )
        return None


def _event_handler(dispatcher: Any) -> Any:
    """构造 lark_oapi 事件分发器，注册消息接收事件。"""
    try:
        import lark_oapi as lark
    except ImportError as exc:
        logger.error("lark_oapi 未安装，无法启动飞书长连接: %s", exc)
        return None

    def on_p2_message_receive_v1(data: Any) -> None:
        """im.message.receive_v1 事件处理（长连接模式下 data 已解析）。"""
        try:
            event = data.event if hasattr(data, "event") else data
            if event is None:
                return
            event_dict = _event_to_dict(event)
            if not event_dict:
                return
            _dispatch_message_event(event_dict)
        except Exception as exc:
            logger.error(
                "飞书长连接：消息事件处理异常 errorType=%s",
                type(exc).__name__,
            )

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_p2_message_receive_v1)
        .build()
    )
    return event_handler


def _event_to_dict(event: Any) -> Optional[dict]:
    """将 lark_oapi 事件对象序列化为 dict（兼容 SDK 不同版本字段访问方式）。"""
    try:
        if hasattr(event, "json"):
            return json.loads(event.json())
    except Exception:
        pass
    try:
        if hasattr(event, "model_dump"):
            return event.model_dump()
    except Exception:
        pass
    try:
        if hasattr(event, "dict"):
            return event.dict()
    except Exception:
        pass
    return None


def _dispatch_message_event(event_dict: dict) -> None:
    """将长连接收到的事件转换为 webhook 兼容 body，复用现有处理链路。"""
    from .feishu_bot import parse_message_event, send_text_message
    from .feishu_chat import handle_feishu_user_message

    body = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": event_dict,
    }
    parsed = parse_message_event(body)
    if not parsed:
        logger.debug("飞书长连接：非文本消息，跳过")
        return

    sender_open_id = parsed.get("sender_open_id") or ""
    content = parsed.get("content") or ""
    chat_id = parsed.get("chat_id") or ""
    if not sender_open_id or not chat_id:
        return

    async def _run_reply() -> None:
        try:
            reply = await handle_feishu_user_message(sender_open_id, content)
            if reply:
                await send_text_message(chat_id, reply)
        except Exception as exc:
            logger.error(
                "飞书长连接：回复消息异常 errorType=%s",
                type(exc).__name__,
            )

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        asyncio.create_task(_run_reply())
    else:
        loop.run_until_complete(_run_reply())


async def start() -> bool:
    """启动飞书长连接客户端（阻塞式，需在后台线程运行）。

    Returns:
        True 表示已成功启动连接流程；False 表示未配置或依赖缺失。
    """
    global _started
    if _started:
        logger.info("飞书长连接已在运行")
        return True

    credentials = await _load_app_credentials()
    if not credentials:
        logger.warning("飞书长连接：未配置 feishu_app，跳过启动")
        return False

    _started = True
    _stop_event = asyncio.Event()
    logger.info("飞书长连接客户端启动中 appId=%s", credentials["appId"])

    def _run_blocking() -> None:
        # lark_oapi 在模块导入时捕获当前线程的事件循环；若在 uvicorn 主
        # 循环内导入，其 ws.Client.start() 会对正在运行的 loop 调用
        # run_until_complete 而报错。因此整个导入/构建/启动都放在本线程，
        # 让 SDK 在自己的事件循环上运行。
        try:
            import lark_oapi as lark
            from lark_oapi.ws import Client as WsClient
        except ImportError as exc:
            logger.error("lark_oapi 未安装，无法启动飞书长连接: %s", exc)
            return

        event_handler = _event_handler(lark)
        if event_handler is None:
            return

        try:
            client = WsClient(
                credentials["appId"],
                credentials["appSecret"],
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )
        except TypeError:
            # 兼容 SDK 版本差异：部分版本参数名为 eventHandler
            client = WsClient(
                credentials["appId"],
                credentials["appSecret"],
                eventHandler=event_handler,
                log_level=lark.LogLevel.INFO,
            )

        global _client
        _client = client
        try:
            client.start()
        except Exception as exc:
            logger.error(
                "飞书长连接客户端运行异常 errorType=%s",
                type(exc).__name__,
            )

    import threading

    threading.Thread(target=_run_blocking, daemon=True, name="feishu-ws-client").start()
    return True


def stop() -> None:
    """停止飞书长连接客户端。

    说明：lark_oapi 的 ws.Client 没有公开的 stop 接口，连接由后台 daemon
    线程持有；这里仅复位状态，线程随进程退出自然结束。
    """
    global _client, _started
    if not _started:
        return
    _client = None
    _started = False
    logger.info("飞书长连接客户端已停止")
