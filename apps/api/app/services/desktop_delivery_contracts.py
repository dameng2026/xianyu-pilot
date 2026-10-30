"""Pure contracts for the original application's local delivery workflow."""
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from app.core.camel import CamelModel


class LocalContract(CamelModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MockPaidOrder(LocalContract):
    account_id: int = Field(gt=0, le=2**63-1)
    external_order_id: str = Field(min_length=1, max_length=200, pattern=r"^\S+$")
    buyer_id: str = Field(min_length=1, max_length=200, pattern=r"^\S+$")
    goods_id: int = Field(gt=0, le=2**63-1)
    sku_id: str = Field(default="", max_length=100)
    quantity: int = Field(default=1, ge=1, le=1000)
    amount_cent: int = Field(default=0, ge=0, le=10**12)


class DeliveryPart(LocalContract):
    kind: Literal["text", "card", "image", "link"]
    content: str = Field(min_length=1, max_length=16000)
    repeat: Literal["order", "item"] = "order"

    @model_validator(mode="after")
    def nonblank(self):
        if not self.content.strip():
            raise ValueError("发货内容不能为空")
        if self.kind == "card" and not any(token in self.content for token in ("{卡密}", "{卡号}", "{密码}")):
            raise ValueError("卡密模板必须包含卡密、卡号或密码占位符")
        if self.kind != "card" and any(token in self.content for token in ("{卡密}", "{卡号}", "{密码}")):
            raise ValueError("卡密占位符只能放在卡密段中")
        return self


class DeliveryPlan(LocalContract):
    version: int = Field(default=1, ge=1)
    rule_id: int | None = Field(default=None, gt=0, le=2**63-1)
    sku_id: str = Field(default="", max_length=100)
    card_group_id: int | None = Field(default=None, gt=0, le=2**63-1)
    confirm_shipment: bool = True
    max_attempts: int = Field(default=3,ge=1,le=20)
    low_stock_threshold: int = Field(default=0,ge=0,le=10000)
    pause_on_low_stock: bool = False
    parts: tuple[DeliveryPart, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def card_allocation(self):
        count = sum(part.kind == "card" for part in self.parts)
        if count > 1 or bool(count) != (self.card_group_id is not None):
            raise ValueError("卡密发货须恰有一个卡密段并绑定分组；多卡套餐尚未开放")
        return self


@dataclass(frozen=True)
class StepResult:
    # not_sent is positive evidence of no external effect, not a generic error.
    status: Literal["confirmed", "not_sent", "unknown"]
    receipt_ref: str = ""
    retry_safe: bool = False

    def __post_init__(self):
        if self.status not in ("confirmed", "not_sent", "unknown"):
            raise ValueError("Invalid delivery result")
        if not isinstance(self.receipt_ref, str) or len(self.receipt_ref) > 1000:
            raise ValueError("Invalid receipt reference")
        if not isinstance(self.retry_safe, bool):
            raise ValueError("Retry safety must be explicit")
        if self.status == "confirmed" and not self.receipt_ref.strip():
            raise ValueError("Confirmed delivery requires receipt evidence")
        if self.retry_safe and self.status != "not_sent":
            raise ValueError("Only definitely unsent operations can retry")
        if self.retry_safe and not self.receipt_ref.strip():
            raise ValueError("A safe retry requires evidence of no external effect")


@dataclass(frozen=True)
class DeliveryWork:
    delivery_id: int
    segment_id: int | None
    attempt_id: int
    operation_id: str
    kind: str
    payload: dict


class MockDeliveryGateway(Protocol):
    """Tests inject this; production startup installs no delivery gateway."""
    mode: Literal["mock"]

    def send(self, work: DeliveryWork) -> StepResult: ...

    def confirm_shipment(self, work: DeliveryWork) -> StepResult: ...
