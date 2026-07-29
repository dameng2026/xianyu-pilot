from typing import Optional, List, Any, Literal
from pydantic import ConfigDict, Field, field_validator
from ..core.camel import CamelModel


class OrderQueryReqDTO(CamelModel):
    xianyu_account_id: Optional[int] = None
    xy_goods_id: Optional[str] = None
    order_status: Optional[int] = None
    page_num: Optional[int] = 1
    page_size: Optional[int] = 20


class ConfirmShipmentReqDTO(CamelModel):
    xianyu_account_id: int
    order_id: str


class ConfirmFreeshippingReqDTO(CamelModel):
    """免拼发货请求 DTO（小刀订单专用）。

    必须提供 item_id 和 buyer_id，闲鱼免拼接口要求这两个字段为整数类型。
    """
    xianyu_account_id: int
    order_id: str
    item_id: Any = None
    buyer_id: Any = None


class SoldOrderSyncReqDTO(CamelModel):
    xianyu_account_id: int


class ManualDeliveryReqDTO(CamelModel):
    model_config = ConfigDict(extra="forbid")

    delivery_mode: Literal["text", "card"] = "text"
    delivery_content: str = Field(min_length=1, max_length=10_000)
    quantity_requested: int = Field(default=1, ge=1, le=100)
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("delivery_content")
    @classmethod
    def normalize_delivery_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("发货内容不能为空")
        return normalized


class OrderVO(CamelModel):
    """适配新 XianyuTradeOrder 实体的 DTO"""
    id: Optional[int] = None
    # 新实体字段
    account_id: Optional[int] = None          # 原 xianyu_account_id
    external_order_id: Optional[str] = None   # 原 order_id
    order_status: Optional[int] = None
    buyer_name: Optional[str] = None
    total_amount: Optional[str] = None        # 原 total_price
    create_time: Optional[str] = None
    pay_time: Optional[str] = None
    # 向后兼容字段
    xianyu_account_id: Optional[int] = None
    xy_goods_id: Optional[str] = None
    order_id: Optional[str] = None
    goods_title: Optional[str] = None
    goods_price: Optional[str] = None
    goods_count: Optional[int] = None
    total_price: Optional[str] = None


class OrderListData(CamelModel):
    records: List[OrderVO] = []
    total: int = 0
    page_num: int = 1
    page_size: int = 20
    pages: int = 0
