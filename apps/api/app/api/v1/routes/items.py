import asyncio
import logging
import math
import uuid
import hashlib
import json
import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update as sql_update, and_, desc
from ....core.database import async_session, get_db
from ....core.response import ResultObject
from ....core.cookie_crypto import decrypt_cookie_if_needed
from ....models.entities import XianyuGoods, XianyuGoodsSyncTask, XianyuAccount, XianyuAccountAuth
from ....schemas.common import (
    ItemListReqDTO, ItemReqDTO, ItemDTO, ItemListRespDTO, ItemDetailRespDTO,
    RefreshItemsRespDTO, DeleteItemRespDTO,
    ItemOperateReqDTO, ItemBatchOperateReqDTO, UpdateItemPriceReqDTO,
)
from .internal import verify_internal_or_current_user as verify_internal_token
from ..deps import get_current_user
from ....services.xianyu_goods_sync import XianyuItemOperator
from ....services.remote_goods_delete import (
    RemoteDeleteError,
    RemoteGoodsDeleteCoordinator,
    SqlRemoteGoodsDeleteStore,
    XianyuRemoteDeleteGateway,
)
from ....services.goods_off_shelf import (
    GoodsOffShelfCoordinator,
    GoodsOffShelfError,
    SqlGoodsOffShelfStore,
    XianyuGoodsOffShelfGateway,
)
from ....services.external_operation import (
    ExternalOperationCommand,
    ExternalOperationCoordinator,
    ExternalOperationError,
    RemoteOperationResult,
    SqlExternalOperationStore,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/item")
_item_sync_tasks: set[asyncio.Task[None]] = set()


def _item_sync_done(task: asyncio.Task[None]) -> None:
    _item_sync_tasks.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        logger.error(
            "商品同步后台任务异常 errorType=%s",
            type(exception).__name__,
        )


async def shutdown_item_sync_tasks() -> None:
    """Cancel and drain explicitly owned long-running item sync jobs."""

    tasks = tuple(_item_sync_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        _item_sync_tasks.difference_update(tasks)


def get_remote_goods_delete_coordinator(
    db: AsyncSession = Depends(get_db),
) -> RemoteGoodsDeleteCoordinator:
    return RemoteGoodsDeleteCoordinator(
        store=SqlRemoteGoodsDeleteStore(db),
        gateway=XianyuRemoteDeleteGateway(db),
    )


def get_goods_off_shelf_coordinator(
    db: AsyncSession = Depends(get_db),
) -> GoodsOffShelfCoordinator:
    return GoodsOffShelfCoordinator(
        store=SqlGoodsOffShelfStore(db),
        gateway=XianyuGoodsOffShelfGateway(db),
    )


def _operation_digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _operation_key(raw: object) -> str:
    candidate = str(raw or "").strip()
    if not candidate:
        raise ExternalOperationError(422, "idempotency_key_required", "幂等键不能为空，请刷新页面后重试")
    if not (16 <= len(candidate) <= 128) or not all(ch.isalnum() or ch in "._:-" for ch in candidate):
        raise ExternalOperationError(422, "invalid_idempotency_key", "幂等键格式无效")
    return candidate


def _operation_response(outcome, *, success_message: str):
    status_code = {
        "confirmed": 200,
        "in_progress": 409,
        "remote_confirmed": 502,
        "failed": 502,
        "unknown": 409,
        "pending": 409,
    }[outcome.status]
    data = outcome.to_data()
    if status_code != 200:
        data.pop("remoteReferenceId", None)
        data.pop("remoteReferenceUrl", None)
    else:
        data["message"] = success_message
        data["itemId"] = data.pop("remoteReferenceId", None)
        data["itemUrl"] = data.pop("remoteReferenceUrl", None)
    return JSONResponse(
        status_code=status_code,
        content=ResultObject(code=status_code, msg=data.get("message") or outcome.message, data=data).model_dump(by_alias=True),
    )


def _db_status_to_fe(db_status: int | None) -> int:
    """
    将 DB 状态约定转换为前端状态约定。
    DB:   1=在售, 0=下架, 2=已售
    FE:   0=在售, 1=下架, 2=已售
    """
    mapping = {1: 0, 0: 1, 2: 2}
    return mapping.get(db_status, db_status or 1)


def goods_to_dto(goods: XianyuGoods) -> ItemDTO:
    """将 XianyuGoods 实体转换为 ItemDTO"""
    return ItemDTO(
        id=goods.id,
        xianyu_account_id=goods.account_id,
        xy_goods_id=goods.external_goods_id,
        goods_title=goods.title,
        goods_price=goods.sold_price or goods.price,
        goods_stock=goods.stock,
        goods_image=goods.cover_pic or goods.image_url,
        cover_pic=goods.cover_pic,
        sold_price=goods.sold_price,
        quantity=goods.quantity,
        exposure_count=goods.exposure_count,
        view_count=goods.view_count,
        want_count=goods.want_count,
        detail_url=goods.detail_url,
        detail_info=goods.detail_info,
        sort_order=goods.sort_order,
        status=_db_status_to_fe(goods.status),
        created_time=str(goods.created_time) if goods.created_time else None,
    )


def normalize_price(price: str) -> str:
    """
    标准化价格字符串。
    验证：不为空、正数、最多2位小数。
    返回标准化后的价格字符串（去除末尾多余的0和小数点）。
    """
    if not price or not price.strip():
        raise ValueError("价格不能为空")

    try:
        value = Decimal(price.strip())
    except Exception:
        raise ValueError(f"价格格式无效: {price}")

    if value <= 0:
        raise ValueError("价格必须大于0")

    # 检查小数位数
    if value.as_tuple().exponent < -2:
        raise ValueError("价格最多保留2位小数")

    # 标准化：去除多余的尾随零（避免 Decimal.normalize() 输出科学计数法）
    normalized = value.quantize(Decimal('0.00'), rounding=ROUND_DOWN)
    normalized_str = str(normalized)
    if '.' in normalized_str:
        normalized_str = normalized_str.rstrip('0').rstrip('.')
    return normalized_str


@router.post("/list", response_model=ResultObject[ItemListRespDTO])
async def list_items(
    req: ItemListReqDTO,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    try:
        page_num = max(req.page_num or 1, 1)
        page_size = max(min(req.page_size or 20, 100), 1)
        valid_account_ids = select(XianyuAccount.id).where(XianyuAccount.deleted == 0)
        query = select(XianyuGoods).where(XianyuGoods.account_id.in_(valid_account_ids))
        if req.xianyu_account_id is not None:
            query = query.where(XianyuGoods.account_id == req.xianyu_account_id)

        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page_num - 1) * page_size
        query = query.order_by(XianyuGoods.id.desc()).offset(offset).limit(page_size)
        result = await db.execute(query)
        items = result.scalars().all()

        item_list = [goods_to_dto(i) for i in items]
        return ResultObject.success(ItemListRespDTO(items=item_list, total=total))
    except Exception as e:
        logger.error("获取商品列表失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/detail", response_model=ResultObject[ItemDetailRespDTO])
async def get_item_detail(
    req: ItemReqDTO,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    try:
        query = select(XianyuGoods)
        if req.xy_goods_id:
            query = query.where(XianyuGoods.external_goods_id == req.xy_goods_id)
        if req.xianyu_account_id is not None:
            query = query.where(XianyuGoods.account_id == req.xianyu_account_id)
        result = await db.execute(query)
        item = result.scalar_one_or_none()
        if not item:
            return ResultObject.failed("商品不存在")
        return ResultObject.success(ItemDetailRespDTO(item=goods_to_dto(item)))
    except Exception as e:
        logger.error("获取商品详情失败", exc_info=True)
        return ResultObject.internal_error()


# ---- 以下为前端兼容性存根端点 ----

@router.post("/refresh")
async def refresh_items(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """
    同步商品：从闲鱼拉取商品列表并入库。
    请求参数: { xianyu_account_id: int }
    返回: { sync_id: str, message: str }
    """
    try:
        account_id = req.get("xianyu_account_id") or req.get("xianyuAccountId")
        if not account_id:
            return ResultObject.failed("缺少参数 xianyu_account_id")

        account_id = int(account_id)
        # 检查是否已有运行中的同步任务：优先返回已有任务，避免重复创建。
        from ....services.xianyu_goods_sync import is_account_syncing
        running_result = await db.execute(
            select(XianyuGoodsSyncTask)
            .where(
                XianyuGoodsSyncTask.account_id == account_id,
                XianyuGoodsSyncTask.deleted == 0,
                XianyuGoodsSyncTask.status.in_(["queued", "running"]),
            )
            .order_by(desc(XianyuGoodsSyncTask.created_time), desc(XianyuGoodsSyncTask.id))
            .limit(1)
        )
        running_task = running_result.scalar_one_or_none()
        if running_task or is_account_syncing(account_id):
            return ResultObject.success({
                "sync_id": running_task.sync_id if running_task else None,
                "status": "running",
                "running": True,
                "message": "该账号已有同步任务正在运行",
            })

        # 获取账号信息
        account_result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == account_id,
            )
        )
        account = account_result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")

        # 获取 Cookie
        auth_result = await db.execute(
            select(XianyuAccountAuth).where(
                XianyuAccountAuth.account_id == account_id,
            )
        )
        auth = auth_result.scalar_one_or_none()
        if not auth or not auth.encrypted_cookie:
            return ResultObject.failed("账号未登录或Cookie已失效，请重新登录")

        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)

        # 生成同步任务ID并落库，避免服务重启后完全丢失任务信息。
        sync_id = str(uuid.uuid4())[:16]
        now = datetime.datetime.now()
        db.add(XianyuGoodsSyncTask(
            sync_id=sync_id,
            account_id=account_id,
            status="queued",
            progress=0,
            started_time=now,
            deleted=0,
            created_time=now,
            updated_time=now,
        ))
        await db.commit()

        # 启动后台同步（不阻塞当前请求）
        from ....services.xianyu_goods_sync import (
            _persist_sync_task,
            sync_goods_for_account,
        )

        async def _run_sync():
            try:
                await sync_goods_for_account(
                    account_id=account_id,
                    cookie_str=cookie_str,
                    sync_id=sync_id,
                    db_session_factory=None,
                    async_fetch_detail=True,
                )
            except asyncio.CancelledError:
                await _persist_sync_task(
                    sync_id,
                    account_id=account_id,
                    status="failed",
                    progress=0,
                    error="服务关闭，同步已中断",
                )
                raise
            except Exception as exc:
                logger.error(
                    "后台同步失败 accountId=%d errorType=%s",
                    account_id,
                    type(exc).__name__,
                )

        # 商品全量同步是显式拥有的长任务，应用关闭时统一取消并等待。
        task = asyncio.create_task(_run_sync(), name="items.goods-sync")
        _item_sync_tasks.add(task)
        task.add_done_callback(_item_sync_done)

        logger.info("商品同步已启动: account_id=%d, sync_id=%s", account_id, sync_id)

        return ResultObject.success({
            "sync_id": sync_id,
            "message": "同步已启动",
        })

    except Exception as e:
        logger.error("启动商品同步失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/publish")
async def publish_item(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """
    发布商品到闲鱼。
    请求参数:
    {
        xianyuAccountId: int,       # 必填，闲鱼账号ID
        title: str,                 # 必填，标题(≤30字)
        description: str,           # 必填，描述(≤5000字)
        imageUrls: list[str],       # 必填，图片URL列表(最多10张)
        price: str|float,           # 必填，价格
        origPrice: str|float,       # 可选，原价
        stock: int,                 # 可选，库存
        category: str,              # 可选，分类名称
        location: str,              # 可选，位置描述
    }
    """
    try:
        account_id = req.get("xianyuAccountId") or req.get("xianyu_account_id")
        if not account_id:
            return ResultObject.failed("缺少参数 xianyuAccountId")
        account_id = int(account_id)
        title = req.get("title", "").strip()
        if not title:
            return ResultObject.failed("宝贝标题不能为空")
        if len(title) > 30:
            return ResultObject.failed("宝贝标题不能超过30个字")

        description = req.get("description", "").strip()
        if not description:
            return ResultObject.failed("宝贝描述不能为空")
        if len(description) > 5000:
            return ResultObject.failed("宝贝描述不能超过5000字")

        image_urls = req.get("imageUrls", [])
        if not image_urls or not isinstance(image_urls, list):
            return ResultObject.failed("请至少上传一张商品图片")

        price = req.get("price", 0)
        try:
            price = float(price)
        except (ValueError, TypeError):
            return ResultObject.failed("价格格式不正确")
        if price <= 0:
            return ResultObject.failed("价格必须大于0")

        stock = req.get("stock", 1)
        try:
            stock = int(stock)
        except (ValueError, TypeError):
            stock = 1

        raw_sku_enabled = req.get("skuEnabled", req.get("sku_enabled", False))
        sku_enabled = str(raw_sku_enabled).strip().lower() not in {
            "",
            "0",
            "false",
            "no",
            "off",
            "none",
        }
        skus = req.get("skus")
        has_sku_payload = skus not in (None, "", [], {})
        if sku_enabled or has_sku_payload:
            raise ExternalOperationError(
                422,
                "multi_sku_not_supported",
                "当前发布执行器仅支持单规格商品；多规格数据未执行且不会被静默丢弃",
            )

        shipping_mode = str(req.get("shippingMode", "free") or "").strip().lower()
        free_shipping = req.get("freeShipping", True)
        free_shipping_enabled = str(free_shipping).strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
            "none",
        }
        if shipping_mode != "free" or not free_shipping_enabled:
            raise ExternalOperationError(
                422,
                "shipping_mode_not_supported",
                "当前发布执行器仅支持单规格包邮；所选运费模式未执行且不会被静默改写",
            )

        # 获取账号 Cookie
        from ....services.xianyu_goods_sync import extract_token_from_cookie
        auth = await _get_account_auth(db, account_id)
        if not auth:
            return ResultObject.failed("账号未登录或 Cookie 已失效，请重新登录")
        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)

        # 校验 Token
        token = extract_token_from_cookie(cookie_str)
        if not token:
            return ResultObject.failed("Cookie 中缺少 _m_h5_tk，请重新登录")

        # 构建发布数据
        item_data = {
            "title": title,
            "desc": description,
            "imageUrls": image_urls,
            "price": price,
            "quantity": stock,
        }

        # 可选字段
        orig_price = req.get("origPrice")
        if orig_price:
            item_data["origPrice"] = orig_price

        # 分类（优先用类目推荐 API，这里传参作为手动回退用）
        category = req.get("category", "")
        if category:
            item_data["category"] = {"catName": category}

        # 位置信息（来自前端 POI 搜索选中的 dict）
        location = req.get("location", {})
        if isinstance(location, dict) and location.get("poiName"):
            item_data["location"] = {
                "prov": location.get("prov", ""),
                "city": location.get("city", ""),
                "area": location.get("area", ""),
                "divisionId": str(location.get("divisionId", "")),
                "gps": location.get("gps", ""),
                "poiId": location.get("poiId", ""),
                "poiName": location.get("poiName", ""),
            }
        elif isinstance(location, str) and location.strip():
            # 兼容旧版：仅传了字符串位置名
            item_data["location"] = {
                "prov": "", "city": "", "area": "", "divisionId": "",
                "gps": "", "poiId": "", "poiName": location,
            }

        # 运费模式
        item_data["shippingMode"] = shipping_mode

        support_self_pick = req.get("supportSelfPick", False)
        item_data["supportSelfPick"] = support_self_pick

        # 先持久化幂等 attempt，再执行平台发布；平台确认后本地落库与
        # attempt 确认共用一次数据库提交。
        from ....services.xianyu_goods_sync import XianyuItemPublisher, persist_published_goods
        publisher = XianyuItemPublisher(cookie_str)
        target_local_id = None
        if req.get("localGoodsId") is not None:
            try:
                target_local_id = int(req.get("localGoodsId"))
            except (TypeError, ValueError):
                raise ExternalOperationError(422, "invalid_local_goods_id", "本地商品标识无效")
            if target_local_id <= 0:
                raise ExternalOperationError(422, "invalid_local_goods_id", "本地商品标识无效")
            target_goods = (
                await db.execute(
                    select(XianyuGoods).where(
                        XianyuGoods.id == target_local_id,
                        XianyuGoods.deleted == 0,
                    )
                )
            ).scalar_one_or_none()
            if target_goods is None:
                raise ExternalOperationError(404, "local_goods_not_found", "待发布的本地商品不存在")
            if target_goods.account_id is not None and int(target_goods.account_id) != int(account_id):
                raise ExternalOperationError(409, "local_goods_account_conflict", "本地商品与发布账号不匹配")

        digest = _operation_digest({
            "accountId": account_id,
            "targetLocalId": target_local_id,
            "payload": item_data,
        })
        idempotency_key = _operation_key(req.get("idempotencyKey") or req.get("idempotency_key"))

        async def remote_publish() -> RemoteOperationResult:
            try:
                result = await asyncio.to_thread(publisher.publish, item_data)
            except RuntimeError:
                return RemoteOperationResult.failed(
                    "platform_rejected",
                    "平台明确拒绝发布，请检查账号状态或商品资料后重试",
                )
            except Exception:
                return RemoteOperationResult.unknown(
                    "publish_result_unknown",
                    "发布结果未知，请先同步商品核对，系统已禁止自动重试",
                )
            if not isinstance(result, dict) or result.get("success") is not True:
                return RemoteOperationResult.failed(
                    "platform_rejected",
                    "平台明确拒绝发布，请检查账号状态或商品资料后重试",
                )
            item_id = str(result.get("itemId") or "").strip()
            if not item_id:
                return RemoteOperationResult.unknown(
                    "publish_reference_missing",
                    "平台返回成功但缺少可核对的商品标识，请先同步商品，禁止重复发布",
                )
            return RemoteOperationResult.confirmed(item_id, str(result.get("itemUrl") or ""))

        async def persist_local(remote_result: RemoteOperationResult) -> int | None:
            persisted = await persist_published_goods(
                db,
                account_id=account_id,
                cookie_str=cookie_str,
                publish_result={
                    "itemId": remote_result.reference_id,
                    "itemUrl": remote_result.reference_url,
                },
                publish_payload=item_data,
                target_goods_id=target_local_id,
            )
            if not persisted:
                raise RuntimeError("local persistence did not produce a record")
            return int(persisted["localId"]) if persisted.get("localId") is not None else None

        outcome = await ExternalOperationCoordinator(
            SqlExternalOperationStore(db),
            remote_publish,
            persist_local,
        ).execute(
            ExternalOperationCommand(
                operation_type="publish",
                account_id=account_id,
                idempotency_key=idempotency_key,
                request_digest=digest,
                target_local_id=target_local_id,
            )
        )
        logger.info("商品发布操作结束: accountId=%d state=%s", account_id, outcome.status)
        return _operation_response(outcome, success_message="发布成功并已保存到本地商品库")

    except ExternalOperationError as e:
        return JSONResponse(
            status_code=e.status_code,
            content=ResultObject(
                code=e.status_code,
                msg=e.public_message,
                data={
                    "status": "failed",
                    "errorCode": e.code,
                    "retrySafe": False,
                },
            ).model_dump(by_alias=True),
        )
    except RuntimeError as e:
        logger.error("商品发布运行错误")
        return ResultObject.internal_error()
    except Exception as e:
        logger.error("商品发布异常")
        return ResultObject.internal_error()


@router.post("/delete")
async def delete_item(
    req: ItemOperateReqDTO,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """
    本地删除商品（标记 deleted=1）。
    """
    try:
        xy_goods_id = req.xy_goods_id
        if not xy_goods_id:
            return ResultObject.failed("缺少商品 ID")

        stmt = (
            sql_update(XianyuGoods)
            .where(
                and_(
                    XianyuGoods.external_goods_id == xy_goods_id,
                )
            )
            .values(deleted=1, updated_time=datetime.datetime.now())
        )
        await db.execute(stmt)
        await db.commit()

        logger.info("本地删除商品: goods_id=%s", xy_goods_id)
        return ResultObject.success({"message": "删除成功"})
    except Exception as exc:
        logger.error("本地删除商品失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/offShelf")
async def off_shelf_item(
    req: ItemOperateReqDTO,
    coordinator: GoodsOffShelfCoordinator = Depends(get_goods_off_shelf_coordinator),
    _: None = Depends(verify_internal_token),
):
    """Execute platform off-shelf once and durably finalize local status."""
    try:
        outcome = await coordinator.execute(
            account_id=int(req.xianyu_account_id or 0),
            external_goods_id=str(req.xy_goods_id or ""),
            idempotency_key=req.idempotency_key,
        )
    except GoodsOffShelfError as exc:
        data = {
            "status": "failed",
            "message": exc.public_message,
            "errorCode": exc.error_code,
            "retrySafe": False,
            **exc.data,
        }
        return JSONResponse(
            status_code=exc.http_status,
            content=ResultObject(
                code=exc.http_status,
                msg=exc.public_message,
                data=data,
            ).model_dump(by_alias=True),
        )

    status_code = {
        "confirmed": 200,
        "in_progress": 409,
        "pending": 409,
        "remote_confirmed": 502,
        "failed": 502,
        "unknown": 409,
    }[outcome.status]
    logger.info(
        "Goods off-shelf state changed attemptId=%d state=%s",
        outcome.attempt_id,
        outcome.status,
    )
    return JSONResponse(
        status_code=status_code,
        content=ResultObject(
            code=status_code,
            msg=outcome.message,
            data=outcome.to_data(),
        ).model_dump(by_alias=True),
    )


@router.post("/remoteDelete")
async def remote_delete_item(
    req: ItemOperateReqDTO,
    coordinator: RemoteGoodsDeleteCoordinator = Depends(get_remote_goods_delete_coordinator),
    _: None = Depends(verify_internal_token),
):
    """Delete once remotely, then atomically finalize the local soft delete."""
    try:
        outcome = await coordinator.execute(
            account_id=int(req.xianyu_account_id or 0),
            external_goods_id=str(req.xy_goods_id or ""),
            idempotency_key=req.idempotency_key,
        )
    except RemoteDeleteError as exc:
        data = {
            "status": "failed",
            "message": exc.public_message,
            "errorCode": exc.error_code,
            "retrySafe": False,
            **exc.data,
        }
        result = ResultObject(code=exc.http_status, msg=exc.public_message, data=data)
        return JSONResponse(
            status_code=exc.http_status,
            content=result.model_dump(by_alias=True),
        )

    status_code = {
        "confirmed": 200,
        "in_progress": 409,
        "pending": 409,
        "remote_confirmed": 502,
        "failed": 502,
        "unknown": 409,
    }[outcome.status]
    result = ResultObject(
        code=status_code,
        msg=outcome.message,
        data=outcome.to_data(),
    )
    logger.info(
        "Remote goods delete state changed attemptId=%d state=%s",
        outcome.attempt_id,
        outcome.status,
    )
    return JSONResponse(
        status_code=status_code,
        content=result.model_dump(by_alias=True),
    )


@router.post("/batch/delete")
async def batch_delete_items(
    req: ItemBatchOperateReqDTO,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """
    批量本地删除商品。
    """
    try:
        account_id = req.xianyu_account_id
        item_ids = req.item_ids
        if not account_id or not item_ids:
            return ResultObject.failed("缺少必要参数")

        stmt = (
            sql_update(XianyuGoods)
            .where(
                and_(
                    XianyuGoods.account_id == account_id,
                    XianyuGoods.external_goods_id.in_(item_ids),
                )
            )
            .values(deleted=1, updated_time=datetime.datetime.now())
        )
        await db.execute(stmt)
        await db.commit()

        logger.info("批量本地删除: account_id=%s, count=%d", account_id, len(item_ids))
        return ResultObject.success({"message": f"批量删除成功，共 {len(item_ids)} 条"})
    except Exception as exc:
        logger.error("批量删除失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/batch/remoteDelete")
async def batch_remote_delete_items(
    req: ItemBatchOperateReqDTO,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """
    批量远程删除商品：逐个调用闲鱼 API 删除。
    """
    try:
        account_id = req.xianyu_account_id
        item_ids = req.item_ids
        if not account_id or not item_ids:
            return ResultObject.failed("缺少必要参数")

        # 获取账号 Cookie
        auth = await _get_account_auth(db, account_id)
        if not auth:
            return ResultObject.failed("账号未登录或 Cookie 已失效")

        is_fish_shop = await _is_fish_shop_account(db, account_id)
        operator = XianyuItemOperator(decrypt_cookie_if_needed(auth.encrypted_cookie), is_fish_shop=is_fish_shop)

        results = operator.delete_batch(item_ids)

        success_ids = [iid for iid, ok in results.items() if ok]
        failed_ids = [iid for iid, ok in results.items() if not ok]

        # 本地标记已删除成功的
        if success_ids:
            stmt = (
                sql_update(XianyuGoods)
                .where(
                    and_(
                        XianyuGoods.account_id == account_id,
                        XianyuGoods.external_goods_id.in_(success_ids),
                    )
                )
                .values(status=3, updated_time=datetime.datetime.now())
            )
            await db.execute(stmt)
            await db.commit()

        logger.info(
            "批量远程删除: account_id=%s, success=%d, failed=%d",
            account_id, len(success_ids), len(failed_ids),
        )

        return ResultObject.success({
            "message": f"删除完成，成功 {len(success_ids)} 条，失败 {len(failed_ids)} 条",
            "success_ids": success_ids,
            "failed_ids": failed_ids,
        })
    except Exception as exc:
        logger.error("批量远程删除失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/updatePrice")
async def update_item_price(
    req: UpdateItemPriceReqDTO,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """
    修改闲鱼商品价格。
    仅鱼小铺账号支持。流程：
    1. 校验参数
    2. 加载商品、账号信息
    3. 检查鱼小铺账号
    4. 获取 Cookie
    5. 标准化价格
    6. 调用闲鱼 API 改价
    7. 更新本地数据库价格
    """
    try:
        account_id = req.xianyu_account_id
        xy_goods_id = req.xy_goods_id
        price = req.price

        # 1. 参数校验
        if not account_id:
            return ResultObject.failed("缺少账号ID")
        if not xy_goods_id:
            return ResultObject.failed("缺少商品ID")
        if not price:
            return ResultObject.failed("缺少价格参数")

        # 2. 加载商品信息
        goods_result = await db.execute(
            select(XianyuGoods).where(
                and_(
                    XianyuGoods.account_id == account_id,
                    XianyuGoods.external_goods_id == xy_goods_id,
                )
            )
        )
        goods = goods_result.scalar_one_or_none()
        if not goods:
            return ResultObject.failed("商品不存在")

        # 3. 加载账号信息，检查鱼小铺
        account_result = await db.execute(
            select(XianyuAccount).where(
                and_(
                    XianyuAccount.id == account_id,
                    XianyuAccount.deleted == 0,
                )
            )
        )
        account = account_result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")

        is_fish_shop = bool(getattr(account, "fish_shop", False))
        if not is_fish_shop:
            return ResultObject.failed("当前账号不是鱼小铺，无法改价")

        # 4. 获取 Cookie
        auth = await _get_account_auth(db, account_id)
        if not auth:
            return ResultObject.failed("未找到账号Cookie，请先登录")
        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)

        # 5. 标准化价格
        try:
            normalized_price = normalize_price(price)
        except ValueError:
            return JSONResponse(
                status_code=422,
                content=ResultObject(code=422, msg="价格格式无效", data=None).model_dump(by_alias=True),
            )

        digest = _operation_digest({
            "accountId": account_id,
            "localGoodsId": int(goods.id),
            "newPrice": normalized_price,
        })
        idempotency_key = _operation_key(req.idempotency_key)
        operator = XianyuItemOperator(cookie_str, is_fish_shop=True)

        async def remote_update_price() -> RemoteOperationResult:
            try:
                await asyncio.to_thread(operator.update_price, xy_goods_id, normalized_price)
            except RuntimeError:
                return RemoteOperationResult.failed(
                    "platform_rejected",
                    "平台明确拒绝改价，请检查账号或商品状态后重试",
                )
            except Exception:
                return RemoteOperationResult.unknown(
                    "price_result_unknown",
                    "改价结果未知，请先同步商品核对，系统已禁止自动重试",
                )
            return RemoteOperationResult.confirmed(str(xy_goods_id), None)

        async def persist_local_price(remote_result: RemoteOperationResult) -> int | None:
            del remote_result
            stmt = (
                sql_update(XianyuGoods)
                .where(XianyuGoods.id == goods.id)
                .values(
                    sold_price=normalized_price,
                    price=normalized_price,
                    updated_time=datetime.datetime.now(),
                )
            )
            await db.execute(stmt)
            return int(goods.id)

        outcome = await ExternalOperationCoordinator(
            SqlExternalOperationStore(db),
            remote_update_price,
            persist_local_price,
        ).execute(
            ExternalOperationCommand(
                operation_type="update_price",
                account_id=int(account_id),
                idempotency_key=idempotency_key,
                request_digest=digest,
                target_local_id=int(goods.id),
            )
        )
        logger.info("商品改价操作结束: accountId=%d state=%s", account_id, outcome.status)
        return _operation_response(outcome, success_message="商品价格已在平台和本地确认")

    except ExternalOperationError as e:
        return JSONResponse(
            status_code=e.status_code,
            content=ResultObject(code=e.status_code, msg=e.public_message, data={"status": "failed", "retrySafe": False}).model_dump(by_alias=True),
        )
    except Exception:
        logger.error("改价失败")
        return ResultObject.internal_error()


@router.post("/updateAutoReplyStatus")
async def update_auto_reply_status(
    request: Request,
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """更新商品自动回复状态（兼容旧接口，实际委派给 auto_reply_scope 模块）。"""
    from app.api.v1.routes.auto_reply_scope import update_product_scope
    return await update_product_scope(request, req, db, _)


@router.get("/syncProgress/{sync_id}")
async def get_sync_progress(
    sync_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    try:
        from ....services.xianyu_goods_sync import get_sync_progress as _get_progress
        progress = _get_progress(sync_id)
        if progress:
            return ResultObject.success(progress)

        result = await db.execute(select(XianyuGoodsSyncTask).where(XianyuGoodsSyncTask.sync_id == sync_id, XianyuGoodsSyncTask.deleted == 0))
        task = result.scalar_one_or_none()
        if not task:
            return ResultObject.success({"progress": 0, "status": "not_found"})
        return ResultObject.success({
            "sync_id": task.sync_id,
            "account_id": task.account_id,
            "status": task.status,
            "progress": task.progress or 0,
            "total": task.total_count or 0,
            "new": task.new_count or 0,
            "updated": task.updated_count or 0,
            "skipped": task.skipped_count or 0,
            "off_shelf": task.off_shelf_count or 0,
            "detail_synced": task.detail_synced_count or 0,
            "duration_seconds": task.duration_seconds or 0,
            "error": task.error_message,
            "started_at": task.started_time.isoformat() if task.started_time else None,
            "finished_at": task.finished_time.isoformat() if task.finished_time else None,
            "source": "db",
        })
    except Exception:
        logger.error("获取同步进度失败 sync_id=%s", sync_id, exc_info=True)
        return ResultObject.internal_error()


@router.get("/syncing/{account_id}")
async def is_syncing(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    try:
        from ....services.xianyu_goods_sync import is_account_syncing as _is_syncing
        if _is_syncing(account_id):
            return ResultObject.success(True)
        query = select(func.count()).select_from(XianyuGoodsSyncTask).where(
            XianyuGoodsSyncTask.account_id == account_id,
            XianyuGoodsSyncTask.deleted == 0,
            XianyuGoodsSyncTask.status.in_(["queued", "running"]),
        )
        result = await db.execute(query)
        return ResultObject.success((result.scalar() or 0) > 0)
    except Exception:
        logger.error("检查同步状态失败 account_id=%d", account_id, exc_info=True)
        return ResultObject.internal_error()


# ==================== 内部辅助函数 ====================


@router.post("/polish")
async def polish_item(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    """
    擦亮商品（每天一次，提升曝光）。
    请求参数: { xianyuAccountId: int, xyGoodsId: str }
    """
    try:
        account_id = req.get("xianyuAccountId") or req.get("xianyu_account_id")
        xy_goods_id = req.get("xyGoodsId") or req.get("xy_goods_id")
        if not account_id or not xy_goods_id:
            return ResultObject.failed("缺少参数 xianyuAccountId 或 xyGoodsId")
        account_id = int(account_id)
        auth = await _get_account_auth(db, account_id)
        if not auth:
            return ResultObject.failed("账号未登录或Cookie已失效")
        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)
        operator = XianyuItemOperator(cookie_str)
        success = await asyncio.to_thread(operator.polish_item, str(xy_goods_id))
        if success:
            return ResultObject.success({"message": "擦亮成功"})
        return ResultObject.failed("擦亮失败，请稍后重试")
    except RuntimeError as e:
        return ResultObject.failed(f"擦亮失败: {e}")
    except Exception as e:
        logger.error("擦亮异常 errorType=%s", type(e).__name__)
        return ResultObject.internal_error()


async def _get_account_auth(db: AsyncSession, account_id: int):
    """获取账号的认证信息（Cookie）"""
    result = await db.execute(
        select(XianyuAccountAuth).where(
            and_(
                XianyuAccountAuth.account_id == account_id,
            )
        )
    )
    auth = result.scalar_one_or_none()
    if not auth or not auth.encrypted_cookie:
        return None
    return auth


async def _is_fish_shop_account(db: AsyncSession, account_id: int) -> bool:
    """
    判断账号是否为鱼小铺账号。
    通过 XianyuAccount 扩展字段判断，默认返回 False（普通账号）。
    如需启用鱼小铺功能，请在数据库账号记录中添加 fish_shop 标记。
    """
    try:
        result = await db.execute(
            select(XianyuAccount).where(
                and_(
                    XianyuAccount.id == account_id,
                    XianyuAccount.deleted == 0,
                )
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            return False
        # 检查是否有 fish_shop 属性（模型扩展字段/实际列）
        return bool(getattr(account, "fish_shop", False))
    except Exception:
        return False


