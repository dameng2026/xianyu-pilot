"""Transport-independent contracts. Importing these never starts the server stack."""
from dataclasses import dataclass
from enum import Enum


class SendStatus(str, Enum):
    CONFIRMED = "confirmed_sent"
    REFUSED = "definitely_not_sent"
    UNKNOWN = "unknown"


SCENARIOS = {
    "normal": "正常回复",
    "split": "分段回复",
    "duplicate": "重复消息",
    "wrong_sender": "混入其他发送者",
    "timeout": "回复超时",
    "refused": "明确未发送",
    "unknown": "发送结果不明",
}


@dataclass(frozen=True)
class Binding:
    account_id: str = "demo-self"
    conversation_id: str = "demo-conversation"
    bot_id: str = "demo-bot"
    policy_version: int = 1


@dataclass(frozen=True)
class Query:
    operation_id: str
    text: str
    scenario: str


@dataclass(frozen=True)
class Receipt:
    operation_id: str
    status: SendStatus


@dataclass(frozen=True)
class Incoming:
    event_id: str
    account_id: str
    conversation_id: str
    sender_id: str
    operation_id: str
    body: str
    part: int = 1
    total: int = 1
