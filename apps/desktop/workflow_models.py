"""Validated product settings. Tenant IDs and runtime targets never come from task input."""
from string import Formatter
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccountInput(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    identity: str = Field(min_length=1, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$")
    driver: Literal["mock", "manual", "onebot"] = "manual"
    endpoint: str = Field(default="http://127.0.0.1:3000", max_length=160)
    credential: str | None = Field(default=None, max_length=4096)
    event_secret: str | None = Field(default=None, min_length=24, max_length=256)

    @model_validator(mode="after")
    def check_identity(self):
        if self.driver == "onebot" and not re.fullmatch(r"[1-9][0-9]{0,19}", self.identity):
            raise ValueError("QQ 接入器需要真实的数字 QQ 账号标识")
        return self


def template_fields(template: str) -> set[str]:
    result = set()
    try:
        for _, field, spec, conversion in Formatter().parse(template):
            if field is not None:
                if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,39}", field) or spec or conversion:
                    raise ValueError("模板只允许 {字段名}，不支持属性、下标或格式表达式")
                result.add(field)
    except ValueError as exc:
        raise ValueError("模板格式错误，请使用 {字段名}") from exc
    return result


class ServiceInput(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    account_id: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=120)
    request_template: str = Field(default="查询 {value}，请求编号 {request_id}", min_length=1, max_length=2000)
    required_fields: list[str] = Field(default_factory=lambda: ["value"], max_length=12)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    retry_limit: int = Field(default=0, ge=0, le=3)
    allow_auto: bool = False
    result_mode: Literal["manual", "regex", "manual_fill"] = "manual"
    result_pattern: str = Field(default="", max_length=200)
    failure_message: str = Field(default="查询暂未完成，卖家正在核对，请稍候。", max_length=500)
    enabled: bool = True

    @model_validator(mode="after")
    def check(self):
        if len(set(self.required_fields)) != len(self.required_fields) or any(
            not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,39}", item) or item in {"request_id", "result", "order_id"}
            for item in self.required_fields
        ):
            raise ValueError("参数名必须唯一，只使用英文字母、数字、下划线，不能使用保留字段")
        if template_fields(self.request_template) - set(self.required_fields) - {"request_id", "order_id"}:
            raise ValueError("请求模板引用了未声明的参数")
        if self.result_mode == "regex" and not self.result_pattern:
            raise ValueError("自动提取结果需要配置正则表达式")
        if self.result_pattern:
            try: re.compile(self.result_pattern)
            except re.error as exc: raise ValueError("结果正则无效") from exc
        return self


class ProductInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    external_id: str = Field(min_length=1, max_length=80)
    mode: Literal["direct", "qq"] = "direct"
    service_id: str | None = None
    delivery_template: str = Field(default="感谢购买。以下是操作说明：请按教程完成后续设置。", min_length=1, max_length=3000)
    parameter_source: Literal["none", "buyer_chat", "seller", "fixed", "extract_review"] = "none"
    fixed_parameters: dict[str, str] = Field(default_factory=dict, max_length=12)
    parameter_pattern: str = Field(default="", max_length=200)
    execution_policy: Literal["auto", "confirm", "threshold"] = "confirm"
    approval_threshold: int = Field(default=0, ge=0, le=100000000)
    review_result: bool = True
    delivery_channel: Literal["mock", "manual"] = "mock"
    shipping_policy: Literal["auto", "manual"] = "manual"
    enabled: bool = True

    @model_validator(mode="after")
    def check(self):
        if self.mode == "qq" and not self.service_id:
            raise ValueError("QQ 处理商品需要选择自己的 QQ 服务")
        if self.mode == "direct":
            self.service_id = None
            self.parameter_source = "none"
        if self.parameter_source == "extract_review" and not self.parameter_pattern:
            raise ValueError("参数自动提取需要配置正则表达式")
        if self.parameter_pattern:
            try: re.compile(self.parameter_pattern)
            except re.error as exc: raise ValueError("参数正则无效") from exc
        for key, value in self.fixed_parameters.items():
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,39}", key) or len(value) > 1000:
                raise ValueError("固定参数格式或长度错误")
        template_fields(self.delivery_template)
        if self.delivery_channel == "manual" and self.shipping_policy == "auto":
            raise ValueError("真实闲鱼接口尚未接入，人工交付必须人工确认发货")
        return self


class OrderInput(StrictModel):
    order_key: str = Field(min_length=1, max_length=100)
    product_id: str = Field(min_length=1, max_length=80)
    paid: bool = True
    amount_fen: int = Field(default=0, ge=0, le=100000000)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=12)
    buyer_text: str = Field(default="", max_length=4000)
    scenario: Literal["normal", "split", "duplicate", "wrong_sender", "timeout", "refused", "unknown"] = "normal"
    parent_id: str | None = Field(default=None, max_length=80)


class TaskAction(StrictModel):
    action: Literal["pay", "parameters", "confirm", "approve", "manual_result", "manual_sent", "confirm_delivery",
                    "confirm_shipping", "cancel", "stop_delivery", "retry", "release_channel", "notify", "edit"]
    expected_revision: int = Field(ge=1)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=12)
    text: str = Field(default="", max_length=4000)
    mode: Literal["direct", "qq"] | None = None
    service_id: str | None = None
    delivery_template: str | None = Field(default=None, max_length=3000)


STATES = {
    "awaiting_payment": "待付款", "awaiting_parameters": "等待买家或卖家提供参数",
    "awaiting_confirmation": "待卖家确认", "queued_qq": "待提交 QQ", "qq_sending": "QQ 提交中",
    "waiting_qq": "已提交 QQ，等待结果", "result_review": "结果待审核",
    "awaiting_delivery": "待回发买家", "delivering": "回发中",
    "awaiting_shipping": "已交付，待确认闲鱼发货", "confirming_shipping": "确认发货中",
    "completed": "已完成", "manual_required": "失败待人工", "cancelled": "已取消",
    "delivery_stopped": "已终止后续交付",
}
