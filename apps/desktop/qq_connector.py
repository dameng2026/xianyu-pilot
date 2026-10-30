"""Project-owned, replaceable QQ transport. No Tulpa code or runtime dependency.

OneBot v11 is an optional wire adapter, not the project's MCP protocol or a QQ login implementation.
"""
import hashlib
import hmac
import http.client
import json
import re
import time
from urllib.parse import urlsplit

from .storage import Conflict, now


class ConnectorError(ValueError):
    pass


def validate_endpoint(value):
    parsed = urlsplit(value)
    try: port = parsed.port
    except ValueError as exc: raise ConnectorError("QQ 接入端口无效") from exc
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path not in {"", "/"}
            or not port or not 1024 <= port <= 65535 or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ConnectorError("QQ 接入地址必须是 http://127.0.0.1:端口")
    return port


class OneBotClient:
    ALLOWED = {"get_login_info", "get_friend_list", "send_private_msg"}

    def __init__(self, endpoint, token):
        self.port = validate_endpoint(endpoint)
        if not token or "\r" in token or "\n" in token:
            raise ConnectorError("请配置自己的 QQ 接入令牌")
        self.token = token

    def call(self, action, params):
        if action not in self.ALLOWED:
            raise ConnectorError("未开放此 QQ 操作")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        try:
            connection.request("POST", "/" + action, body=json.dumps(params).encode(), headers={
                "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 1024 * 1024:
                raise ConnectorError("QQ 接入返回异常，请核对连接和回执")
            result = json.loads(raw)
            if not isinstance(result, dict) or "data" not in result:
                raise ConnectorError("QQ 接入响应格式无效")
            if result.get("status") != "ok" or result.get("retcode") != 0:
                raise ConnectorError("QQ 接入未确认操作成功，请核对原操作")
            return result["data"]
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if isinstance(exc, ConnectorError): raise
            raise ConnectorError("本机 QQ 连接失败；发送请求不会自动重试") from exc
        finally:
            connection.close()

    def identity(self, expected):
        result = self.call("get_login_info", {})
        if not isinstance(result, dict) or str(result.get("user_id")) != expected:
            raise ConnectorError("QQ 当前登录账号与登记账号不一致")

    def friends(self, expected):
        self.identity(expected)
        result = self.call("get_friend_list", {})
        if not isinstance(result, list) or len(result) > 500:
            raise ConnectorError("好友目录格式或数量超过首版核对范围")
        return [{"conversation_id": f"{expected}:direct:{item['user_id']}",
                 "name": str(item.get("remark") or item.get("nickname") or item["user_id"])[:80]}
                for item in result if isinstance(item, dict) and re.fullmatch(r"[1-9][0-9]{0,19}", str(item.get("user_id", "")))]


def event_state(repository, account_id):
    with repository.store.connect() as db:
        row = db.execute("SELECT * FROM wf_connector_state WHERE tenant_id=? AND account_id=?", (repository.tenant, account_id)).fetchone()
        return dict(row) if row else {"last_seen": 0, "dropped_through": 0}


def verify_account(repository, identity):
    row, credentials = repository.account_private(identity)
    if row["status"] == "unbound": raise ConnectorError("账号已解绑，请重新添加")
    if row["driver"] == "mock":
        evidence, ok = {"source": "mock", "note": "仅验证本地模拟器，不代表真实 QQ 在线", "friends": []}, True
    elif row["driver"] == "manual":
        evidence, ok = {"source": "manual", "note": "人工登记不能证明账号归属，仅允许手动操作", "friends": []}, False
    else:
        try:
            client = OneBotClient(row["endpoint"], credentials.get("api_token", ""))
            friends = client.friends(row["identity"])
            ok = bool(credentials.get("event_secret")) and time.time() - event_state(repository, identity)["last_seen"] < 90
            evidence = {"source": "onebot", "identity": row["identity"], "friends": friends,
                        "note": "已核对登录账号、好友目录和签名事件通道" if ok else "登录账号已核对，等待配置签名事件通道并收到心跳；自动提交保持关闭"}
        except ConnectorError as exc:
            evidence, ok = {"source": "onebot", "note": str(exc), "friends": []}, False
    return repository.record_verification(identity, row["generation"], evidence, ok)


def accept_event(repository, account_id, raw: bytes, signature: str):
    row, credentials = repository.account_private(account_id)
    secret = credentials.get("event_secret", "")
    if row["driver"] != "onebot" or row["status"] == "unbound" or not secret:
        raise ConnectorError("此账号没有启用签名事件接入")
    expected = "sha1=" + hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise ConnectorError("事件签名无效")
    event = json.loads(raw)
    if not isinstance(event, dict) or str(event.get("self_id")) != row["identity"]:
        raise ConnectorError("事件账号归属不匹配")
    stamp = event.get("time")
    if not isinstance(stamp, (int, float)) or abs(time.time() - stamp) > 300:
        raise ConnectorError("事件时间无效或过期")
    if event.get("post_type") == "meta_event" and event.get("meta_event_type") == "heartbeat":
        message = None
    elif event.get("post_type") == "message" and event.get("message_type") == "private":
        sender, message_key = str(event.get("user_id", "")), str(event.get("message_id", ""))
        if not re.fullmatch(r"[1-9][0-9]{0,19}", sender) or not re.fullmatch(r"-?[0-9]{1,30}", message_key):
            raise ConnectorError("事件发送者或消息编号无效")
        parts, reply_to = [], None
        segments = event.get("message")
        if not isinstance(segments, list) or len(segments) > 32:
            raise ConnectorError("仅支持有界的纯文本消息段")
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict): raise ConnectorError("事件消息段无效")
            if segment.get("type") == "text": parts.append(str(segment["data"].get("text", "")))
            elif segment.get("type") == "reply": reply_to = str(segment["data"].get("id", ""))
            else: raise ConnectorError("首版只接收文本与引用，不把附件占位当作结果")
        content = "".join(parts)
        if not content or len(content) > 8000: raise ConnectorError("事件正文为空或过长")
        message = (message_key, sender, content, reply_to)
    else:
        raise ConnectorError("该事件类型不在接入范围内")
    with repository.transaction() as db:
        current = repository._get(db, "account", account_id)
        if current["generation"] != row["generation"] or current["status"] == "unbound": raise Conflict("账号配置已改变")
        db.execute("INSERT INTO wf_connector_state(tenant_id,account_id,last_seen) VALUES (?,?,?) ON CONFLICT(tenant_id,account_id) DO UPDATE SET last_seen=excluded.last_seen", (repository.tenant, account_id, time.time()))
        if message:
            db.execute("INSERT OR IGNORE INTO wf_connector_events(tenant_id,account_id,generation,message_key,sender_id,content,reply_to,received) VALUES (?,?,?,?,?,?,?,?)",
                       (repository.tenant, account_id, row["generation"], *message, time.time()))
            cutoff = db.execute("SELECT id FROM wf_connector_events WHERE tenant_id=? AND account_id=? ORDER BY id DESC LIMIT 1 OFFSET 999", (repository.tenant, account_id)).fetchone()
            if cutoff:
                dropped = db.execute("SELECT max(id) FROM wf_connector_events WHERE tenant_id=? AND account_id=? AND id<?", (repository.tenant, account_id, cutoff[0])).fetchone()[0]
                if dropped:
                    db.execute("UPDATE wf_connector_state SET dropped_through=max(dropped_through,?) WHERE tenant_id=? AND account_id=?", (dropped, repository.tenant, account_id))
                    db.execute("DELETE FROM wf_connector_events WHERE tenant_id=? AND account_id=? AND id<=?", (repository.tenant, account_id, dropped))
    return {"accepted": True}
