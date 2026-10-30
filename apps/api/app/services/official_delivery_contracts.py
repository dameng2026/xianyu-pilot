"""Strict platform facts and unified configuration. Never accepted as HTTP payment proof."""
from typing import Literal
from pydantic import Field, model_validator
from app.services.desktop_delivery_contracts import LocalContract, DeliveryPlan

WAITING = "WAIT_SELLER_SEND_GOODS"
SHIPPED = frozenset({"WAIT_BUYER_CONFIRM_GOODS", "TRADE_FINISHED"})


class OfficialOrder(LocalContract):
    account_id: int = Field(gt=0)
    seller_id: str = Field(min_length=1,max_length=200,pattern=r"^\S+$")
    order_id: str = Field(min_length=1,max_length=200,pattern=r"^\S+$")
    buyer_id: str = Field(min_length=1,max_length=200,pattern=r"^\S+$")
    item_id: str = Field(min_length=1,max_length=200,pattern=r"^\S+$")
    sku_id: str = Field(default="",max_length=100)
    quantity: int = Field(strict=True,ge=1,le=1000)
    amount_cent: int = Field(strict=True,ge=0,le=10**12)
    status: str = Field(min_length=1,max_length=100)
    refunding: bool = Field(strict=True)


class TriggerPolicy(LocalContract):
    mode: Literal["payment","payment_keyword"] = "payment"
    keywords: tuple[str,...] = Field(default=(),max_length=20)

    @model_validator(mode="after")
    def validate_words(self):
        if any(not word.strip() or len(word)>100 for word in self.keywords):
            raise ValueError("触发词不能为空或超过 100 字符")
        if self.mode == "payment_keyword" and not self.keywords:
            raise ValueError("付款加关键词模式必须设置触发词")
        return self


class RuleVariant(LocalContract):
    sku_id: str = Field(default="",max_length=100)
    enabled: bool = False
    plan: DeliveryPlan
    trigger: TriggerPolicy = TriggerPolicy()

    @model_validator(mode="after")
    def same_sku(self):
        if self.sku_id != self.plan.sku_id:
            raise ValueError("SKU 与发货计划不一致")
        return self


class GoodsDeliveryConfig(LocalContract):
    variants: tuple[RuleVariant,...] = Field(max_length=100)
    expected_version: int | None = Field(default=None,ge=0)

    @model_validator(mode="after")
    def unique_skus(self):
        if len({variant.sku_id for variant in self.variants}) != len(self.variants):
            raise ValueError("SKU 不能重复")
        return self


class IncomingNotice(LocalContract):
    message_id: str = Field(min_length=1,max_length=200)
    kind: Literal["order_hint","buyer_text"]
    order_id: str = Field(default="",max_length=200)
    item_id: str = Field(default="",max_length=200)
    buyer_id: str = Field(default="",max_length=200)
    session_id: str = Field(default="",max_length=200)
    text: str = Field(default="",max_length=4000)
