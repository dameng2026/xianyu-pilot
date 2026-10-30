"""SQLite adapters for the existing Vue warehouse and legacy rule contracts."""
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi.responses import Response
from pydantic import ConfigDict, Field, field_validator, model_validator, ValidationError

from app.core.camel import CamelModel
from app.core.response import ResultObject
from app.services.desktop_inventory import DesktopInventory, InventoryError, parse_item
from app.services.desktop_delivery_contracts import DeliveryPlan
from app.services.official_delivery_contracts import TriggerPolicy, GoodsDeliveryConfig


class InventoryBody(CamelModel):
    model_config = ConfigDict(extra="forbid")


class GroupBody(InventoryBody):
    group_name: str = Field(min_length=1, max_length=200)
    card_type: Literal["unique", "kami", "card_password", "link_code", "account_password", "custom"] = "unique"
    card_prefix: str = Field(default="", max_length=200)
    password_prefix: str = Field(default="", max_length=200)
    cost_price: float = Field(default=0, ge=0, allow_inf_nan=False)
    suggested_price: float = Field(default=0, ge=0, allow_inf_nan=False)
    alert_threshold: int = Field(default=10, ge=0, le=1000000)
    remark: str | None = Field(default="", max_length=4000)
    status: Literal[0, 1] = 1

    @field_validator("group_name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("分组名称不能为空")
        return value.strip()

    def values(self, *, partial=False):
        values = self.model_dump(by_alias=False, exclude_unset=partial)
        if "card_type" in values:
            values["group_type"] = values.pop("card_type")
            if values["group_type"] == "kami":
                values["group_type"] = "unique"
        return values


class ItemBody(InventoryBody):
    content: str = Field(default="", max_length=32004)
    card_content: str = Field(default="", max_length=16000)
    password: str = Field(default="", max_length=16000)
    extra_info: str = Field(default="", max_length=16000)


class BatchBody(InventoryBody):
    items: list[ItemBody] = Field(min_length=1, max_length=5000)


class RuleBody(InventoryBody):
    rule_name: str = Field(min_length=1, max_length=200)
    account_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    goods_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    delivery_mode: Literal["text", "card", "kami"] = "text"
    card_group_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    reply_content: str = Field(default="", max_length=16000)
    match_keywords: str = Field(default="", max_length=200)
    match_type: Literal["keyword"] = "keyword"
    trigger_on_pay: Literal[0, 1] = 1
    max_delivery_per_day: int = Field(default=0, ge=0, le=1000000)
    status: Literal[0, 1] = 0
    plan: DeliveryPlan | None = None
    trigger: TriggerPolicy | None = None
    sku_id: str = Field(default="",max_length=100)

    @model_validator(mode="after")
    def validate_rule(self):
        self.rule_name = self.rule_name.strip()
        if not self.rule_name:
            raise ValueError("规则名称不能为空")
        if self.plan is not None:
            return self
        if self.delivery_mode in ("card", "kami"):
            if self.card_group_id is None:
                raise ValueError("卡密规则必须选择卡密分组")
        elif not self.reply_content.strip():
            raise ValueError("文字规则必须填写发货内容")
        elif self.card_group_id is not None:
            raise ValueError("文字规则不能绑定卡密分组")
        return self

    def values(self):
        values = self.model_dump(by_alias=False)
        values.pop("match_type")
        values["delivery_content"] = values.pop("reply_content")
        values["trigger_keyword"] = values.pop("match_keywords")
        return values


def inventory(request: Request):
    return DesktopInventory(request.app.state.desktop_store.path)


Inventory = Annotated[DesktopInventory, Depends(inventory)]
Id = Annotated[int, Path(ge=1, le=2**63 - 1)]
Current = Annotated[int, Query(ge=1, le=1000000)]
Size = Annotated[int, Query(ge=1, le=200)]
Status = Annotated[int | None, Query(ge=0, le=4)]


def local_write_boundary(request: Request):
    # Neither JSON Content-Type nor the original client's X-Request-Id can be
    # sent by HTML forms or no-cors browser requests. Axios removes Content-Type
    # on bodyless actions, so its existing custom header is accepted as well.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        is_json = request.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "application/json"
        if not is_json and not request.headers.get("x-request-id"):
            raise InventoryError("本地写入请求必须使用 application/json 或 X-Request-Id", 415)


router = APIRouter(prefix="/api", dependencies=[Depends(local_write_boundary)])


@router.get("/cards/alerts")
def alerts(repo: Inventory):
    return ResultObject.success(repo.list_groups("", 1, 200, alerts=True))


@router.post("/cards/import/validate")
def validate_import(body: BatchBody):
    invalid = 0
    identities = set()
    duplicates = 0
    for raw in body.items:
        try:
            key, value, _ = parse_item(raw.model_dump())
        except InventoryError:
            invalid += 1
            continue
        if (key, value) in identities:
            duplicates += 1
        identities.add((key, value))
    return ResultObject.success({"total": len(body.items), "valid": len(body.items) - invalid,
                                 "invalid": invalid, "duplicateCount": duplicates})


@router.get("/cards")
def groups(repo: Inventory, keyword: str = Query(default="", max_length=200), current: Current = 1, size: Size = 20):
    return ResultObject.success(repo.list_groups(keyword, current, size))


@router.post("/cards")
def create_group(body: GroupBody, repo: Inventory):
    return ResultObject.success(repo.save_group(body.values()), "卡密分组已创建")


@router.put("/cards/{group_id}")
def update_group(group_id: Id, body: GroupBody, repo: Inventory):
    repo.save_group(body.values(partial=True), group_id)
    return ResultObject.success(None, "卡密分组已更新")


@router.delete("/cards/{group_id}")
def delete_group(group_id: Id, repo: Inventory):
    repo.delete_group(group_id)
    return ResultObject.success(None, "卡密分组已删除")


@router.get("/cards/{group_id}")
def group_detail(group_id: Id, repo: Inventory):
    return ResultObject.success(repo.get_group(group_id))


@router.get("/cards/{group_id}/stats")
def stock(group_id: Id, repo: Inventory):
    return ResultObject.success(repo.get_group(group_id, stats=True))


@router.get("/cards/{group_id}/items")
def items(group_id: Id, repo: Inventory, status: Status = None, current: Current = 1, size: Size = 50):
    return ResultObject.success(repo.list_items(group_id, status, current, size))


@router.post("/cards/{group_id}/items")
def create_item(group_id: Id, body: ItemBody, repo: Inventory):
    repo.import_items(group_id, [body.model_dump()], single=True)
    return ResultObject.success(None, "卡密已创建")


@router.post("/cards/{group_id}/items/batch")
def import_items(group_id: Id, body: BatchBody, repo: Inventory):
    return ResultObject.success(repo.import_items(group_id, [item.model_dump() for item in body.items]))


@router.delete("/cards/{group_id}/items/{item_id}")
def delete_item(group_id: Id, item_id: Id, repo: Inventory):
    repo.change_item(group_id, item_id, "delete")
    return ResultObject.success(None, "卡密已删除")


@router.post("/cards/{group_id}/items/{item_id}/reset")
def reset_item(group_id: Id, item_id: Id, repo: Inventory):
    repo.change_item(group_id, item_id, "reset")
    return ResultObject.success(None, "卡密已重置")


@router.post("/cards/{group_id}/items/{item_id}/lock")
def lock_item(group_id: Id, item_id: Id, repo: Inventory):
    repo.change_item(group_id, item_id, "lock")
    return ResultObject.success(None, "卡密已锁定")


@router.post("/cards/{group_id}/items/{item_id}/invalid")
def invalid_item(group_id: Id, item_id: Id, repo: Inventory):
    repo.change_item(group_id, item_id, "invalid")
    return ResultObject.success(None, "卡密已作废")


@router.get("/cards/{group_id}/usage")
def usage(group_id: Id, repo: Inventory, current: Current = 1, size: Size = 20):
    return ResultObject.success(repo.list_items(group_id, None, current, size, usage=True))


@router.get("/cards/{group_id}/export")
def export(group_id: Id, repo: Inventory, status: Status = None):
    rows = repo.list_items(group_id, status, 1, 200, export=True)
    return Response("\n".join(row["content"] for row in rows), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="cards-{group_id}.txt"'})


@router.get("/auto-delivery/rules")
def rules(
    repo: Inventory,
    accountId: int | None = Query(default=None, gt=0, le=2**63 - 1),
    keyword: str = Query(default="", max_length=200),
    current: Current = 1,
    size: Size = 20,
):
    return ResultObject.success(repo.list_rules(accountId, current, size, keyword=keyword))


@router.post("/auto-delivery/rules")
def create_rule(body: RuleBody, repo: Inventory):
    return ResultObject.success(repo.save_rule(body.values()), "发货规则已保存")


@router.put("/auto-delivery/rules/{rule_id}")
def update_rule(rule_id: Id, body: RuleBody, repo: Inventory):
    return ResultObject.success(repo.save_rule(body.values(), rule_id), "发货规则已更新")


@router.delete("/auto-delivery/rules/{rule_id}")
def delete_rule(rule_id: Id, repo: Inventory):
    repo.delete_rule(rule_id)
    return ResultObject.success(None, "发货规则已删除")


@router.get("/auto-delivery/goods/{goods_id}/config")
def goods_config(goods_id: Id,repo:Inventory):
    from app.services.unified_delivery_rules import UnifiedDeliveryRules
    return ResultObject.success(UnifiedDeliveryRules(repo.path).get_goods(goods_id))


@router.put("/auto-delivery/goods/{goods_id}/config")
def save_goods_config(goods_id:Id,repo:Inventory,body:dict=Body(...)):
    from app.services.unified_delivery_rules import UnifiedDeliveryRules
    rules=UnifiedDeliveryRules(repo.path)
    try:
        config=rules.translate_goods_body(goods_id,body)
    except ValidationError as exc:
        raise InventoryError("商品规则格式无效，保存已拒绝") from exc
    return ResultObject.success(rules.save_goods(goods_id,config))
