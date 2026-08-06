"""
售整自动上架重发服务（核心，开源版单租户适配版）。

职责：
1. 扫描符合条件的"售整"商品候选：auto_relist_enabled=1 且 has_snapshot=1；
2. 加载发布快照（xianyu_goods_edit_snapshot）；
3. 按账号类型调用对应发布接口（鱼小铺多规格 / 普通单规格）；
4. 重发成功后：
   - 旧商品记录：标记 next_relist_goods_id、last_relist_at、status=2（已售）；
   - 新商品记录：写入 relist_source_goods_id、继承 auto_relist_enabled=1、has_snapshot=1、original_quantity；
   - 保存新快照，确保新商品被卖出后仍可继续重发（链式重发）。

候选判定规则（满足全部条件）：
- auto_relist_enabled = 1
- has_snapshot = 1
- status IN (0, 2)  # 已售或下架
- next_relist_goods_id IS NULL  # 还没重发过
- original_quantity = 1  # 仅"售整"场景（库存为1被买走）

调用方式：
- 由 relist_scheduler 每 3 分钟轮询调用 scan_and_relist();
- 由 xianyu_order_sync 订单同步钩子在检测到售出订单时调用 relist_sold_item();
- 由 /api/item/republish 路由手动触发 manual_relist()。

说明：本文件由商业版 relist_service.py 移植，去除 tenant_id 维度（开源版单租户）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update, and_

from ..core.database import async_session
from ..core.cookie_crypto import decrypt_cookie_if_needed
from ..models.entities import (
    XianyuAccount,
    XianyuAccountAuth,
    XianyuGoods,
    XianyuGoodsEditSnapshot,
)
from .xianyu_goods_sync import (
    XianyuItemPublisher,
    persist_published_goods,
    extract_token_from_cookie,
)
from .fish_shop_publish import (
    FISH_SHOP_PUBLISH_API,
    FISH_SHOP_PUBLISH_VERSION,
    build_internal_item_object,
    call_fish_shop_api,
    extract_response_item_id,
    validate_multi_spec_payload,
)

logger = logging.getLogger(__name__)


# 单次扫描每个账号最多重发多少件（避免一次性发布太多触发风控）
MAX_RELIST_PER_ACCOUNT_PER_SCAN = 3

# 单次扫描最多处理多少个账号
MAX_ACCOUNTS_PER_SCAN = 20


async def _get_account_auth(
    db,
    account_id: int,
) -> tuple[Optional[XianyuAccountAuth], bool, Optional[XianyuAccount]]:
    """获取账号 auth 与鱼小铺标识。返回 (auth, is_fish_shop, account)。"""
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
        return None, False, None
    is_fish_shop = bool(getattr(account, "fish_shop_user", 0))

    result = await db.execute(
        select(XianyuAccountAuth).where(
            XianyuAccountAuth.account_id == account_id,
        )
    )
    auth = result.scalar_one_or_none()
    return auth, is_fish_shop, account


async def _load_snapshot(
    db,
    account_id: int,
    external_goods_id: str,
) -> Optional[dict]:
    """加载最新的发布快照。"""
    result = await db.execute(
        select(XianyuGoodsEditSnapshot)
        .where(
            and_(
                XianyuGoodsEditSnapshot.account_id == account_id,
                XianyuGoodsEditSnapshot.external_goods_id == str(external_goods_id),
                XianyuGoodsEditSnapshot.deleted == 0,
            )
        )
        .order_by(XianyuGoodsEditSnapshot.created_time.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        return None
    snap_json = snapshot.snapshot_json
    if isinstance(snap_json, str):
        try:
            return json.loads(snap_json)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(snap_json, dict):
        return snap_json
    return None


async def _find_relist_candidates(
    db,
    account_id: Optional[int] = None,
    limit: int = MAX_RELIST_PER_ACCOUNT_PER_SCAN,
) -> list:
    """查找符合重发条件的商品候选。

    条件：
    - auto_relist_enabled = 1
    - has_snapshot = 1
    - status IN (0, 2)  # 下架或已售
    - next_relist_goods_id IS NULL  # 还没重发过
    - original_quantity = 1  # 仅"售整"场景
    - deleted = 0
    """
    conditions = [
        XianyuGoods.auto_relist_enabled == 1,
        XianyuGoods.has_snapshot == 1,
        XianyuGoods.status.in_([0, 2]),
        XianyuGoods.next_relist_goods_id.is_(None),
        XianyuGoods.original_quantity == 1,
        XianyuGoods.deleted == 0,
    ]
    if account_id is not None:
        conditions.append(XianyuGoods.account_id == account_id)

    result = await db.execute(
        select(XianyuGoods)
        .where(and_(*conditions))
        .order_by(XianyuGoods.updated_time.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _save_relist_snapshot(
    db,
    account_id: int,
    external_goods_id: str,
    snapshot_data: dict,
    is_fish_shop: bool,
) -> None:
    """为重发后的新商品保存快照（链式重发支持）。"""
    account_type = "fish_shop" if is_fish_shop else "normal"
    snapshot = XianyuGoodsEditSnapshot(
        account_id=account_id,
        external_goods_id=str(external_goods_id),
        snapshot_json=snapshot_data,
        source="relist",
        account_type=account_type,
    )
    db.add(snapshot)
    await db.flush()


async def _publish_via_fish_shop(
    cookie_str: str,
    snapshot: dict,
    account_id: int,
) -> dict:
    """鱼小铺多规格重发。"""
    # 构建发布请求体（从快照恢复）
    publish_req = {
        "title": snapshot.get("title", ""),
        "description": snapshot.get("description", ""),
        "imageUrls": snapshot.get("imageUrls", []),
        "price": snapshot.get("price", ""),
        "shippingMode": snapshot.get("shippingMode", "free"),
        "supportSelfPick": snapshot.get("supportSelfPick", False),
        "location": snapshot.get("location", {}),
        "category": snapshot.get("category", {}),
    }
    if snapshot.get("itemSkuList"):
        publish_req["itemSkuList"] = snapshot.get("itemSkuList")
    if snapshot.get("itemProperties"):
        publish_req["itemProperties"] = snapshot.get("itemProperties")
    if snapshot.get("origPrice"):
        publish_req["origPrice"] = snapshot.get("origPrice")
    if snapshot.get("postFee"):
        publish_req["postFee"] = snapshot.get("postFee")

    # 校验
    validation_error = validate_multi_spec_payload(publish_req)
    if validation_error:
        raise RuntimeError(f"鱼小铺重发校验失败: {validation_error}")

    # 类目信息：使用快照中的或默认值
    category_info = {
        "catId": (snapshot.get("category") or {}).get("catId") or "50025461",
        "catName": (snapshot.get("category") or {}).get("catName") or "软件安装包/序列号/激活码",
        "channelCatId": (snapshot.get("category") or {}).get("channelCatId") or "201449620",
        "tbCatId": (snapshot.get("category") or {}).get("tbCatId") or "50003316",
        "cardList": (snapshot.get("category") or {}).get("cardList") or [],
    }

    # 复用 XianyuItemPublisher 上传图片（避免重复实现）
    publisher = XianyuItemPublisher(cookie_str)
    image_urls = publish_req.get("imageUrls", [])
    # 快照中可能为 http:// 的闲鱼 CDN 图片，归一化为 https；且 upload 内部使用 asyncio.run()，
    # 必须在线程中执行（本函数处于 async 上下文）
    upload_sources = [
        u.replace("http://", "https://", 1) if str(u).startswith("http://") else u
        for u in image_urls
    ]
    xianyu_image_urls = await asyncio.to_thread(publisher.upload_images_to_xianyu, upload_sources)
    if not xianyu_image_urls:
        raise RuntimeError("鱼小铺重发图片上传失败")

    internal_obj = build_internal_item_object(publish_req, xianyu_image_urls, category_info, is_edit=False)
    result = call_fish_shop_api(
        cookie_str, FISH_SHOP_PUBLISH_API, FISH_SHOP_PUBLISH_VERSION,
        internal_obj, is_edit=False,
    )
    new_item_id = extract_response_item_id(result)
    if not new_item_id:
        raise RuntimeError("鱼小铺重发成功但未返回 itemId")

    return {
        "itemId": str(new_item_id),
        "itemUrl": f"https://www.goofish.com/item?itemId={new_item_id}",
        "publishPayload": publish_req,
    }


async def _publish_via_normal(
    cookie_str: str,
    snapshot: dict,
    account_id: int,
) -> dict:
    """普通账号单规格重发。"""
    # 构建发布请求体
    item_data = {
        "title": snapshot.get("title", ""),
        "desc": snapshot.get("description", ""),
        "imageUrls": snapshot.get("imageUrls", []),
        "price": snapshot.get("price", ""),
        "quantity": 1,  # 普通账号固定库存为 1
    }
    if snapshot.get("category"):
        item_data["category"] = snapshot.get("category")
    if snapshot.get("location"):
        item_data["location"] = snapshot.get("location")
    if snapshot.get("shippingMode"):
        item_data["shippingMode"] = snapshot.get("shippingMode")
    if snapshot.get("supportSelfPick"):
        item_data["supportSelfPick"] = snapshot.get("supportSelfPick")
    if snapshot.get("origPrice"):
        item_data["origPrice"] = snapshot.get("origPrice")
    if snapshot.get("postFee"):
        item_data["postFee"] = snapshot.get("postFee")

    publisher = XianyuItemPublisher(cookie_str)
    # publish 内部使用 asyncio.run() 下载图片，必须在线程中执行（本函数处于 async 上下文）
    result = await asyncio.to_thread(publisher.publish, item_data)
    if not result.get("success"):
        raise RuntimeError(result.get("message", "普通账号重发失败"))

    return {
        "itemId": str(result.get("itemId", "")),
        "itemUrl": result.get("itemUrl", ""),
        "publishPayload": item_data,
    }


async def _do_relist_one(
    db,
    goods,
) -> dict:
    """重发单个商品。

    返回: {"success": bool, "itemId": str, "message": str, "goods_id": int}
    本函数内部不 commit，由调用方控制事务。
    """
    account_id = goods.account_id
    external_goods_id = str(goods.external_goods_id or "")

    # 1) 加载快照
    snapshot = await _load_snapshot(db, account_id, external_goods_id)
    if not snapshot:
        return {
            "success": False,
            "itemId": "",
            "message": "未找到发布快照，无法重发",
            "goods_id": goods.id,
        }

    # 2) 获取账号 auth
    auth, is_fish_shop, _account = await _get_account_auth(db, account_id)
    if not auth or not auth.encrypted_cookie:
        return {
            "success": False,
            "itemId": "",
            "message": "账号未登录或 Cookie 已失效",
            "goods_id": goods.id,
        }

    cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)
    token = extract_token_from_cookie(cookie_str)
    if not token:
        return {
            "success": False,
            "itemId": "",
            "message": "Cookie 中缺少 _m_h5_tk",
            "goods_id": goods.id,
        }

    # 3) 按账号类型调用对应发布接口
    try:
        if is_fish_shop:
            publish_result = await _publish_via_fish_shop(
                cookie_str, snapshot, account_id,
            )
        else:
            publish_result = await _publish_via_normal(
                cookie_str, snapshot, account_id,
            )
    except Exception as e:
        logger.warning(
            "重发调用发布接口失败 goods_id=%s external_goods_id=%s err=%s",
            goods.id, external_goods_id, str(e)[:200],
        )
        return {
            "success": False,
            "itemId": "",
            "message": f"发布接口调用失败: {str(e)[:200]}",
            "goods_id": goods.id,
        }

    new_item_id = publish_result.get("itemId", "")
    if not new_item_id:
        return {
            "success": False,
            "itemId": "",
            "message": "发布成功但未返回 itemId",
            "goods_id": goods.id,
        }

    # 4) 持久化新商品记录到 xianyu_goods
    publish_payload = publish_result.get("publishPayload", {})
    try:
        persisted = await persist_published_goods(
            db,
            account_id=account_id,
            cookie_str=cookie_str,
            publish_result={"itemId": new_item_id, "itemUrl": publish_result.get("itemUrl", "")},
            publish_payload=publish_payload,
        )
    except Exception as e:
        logger.warning(
            "重发后持久化新商品失败 goods_id=%s new_item_id=%s err=%s",
            goods.id, new_item_id, str(e)[:200],
        )
        # 即使持久化失败，商品已经发布到闲鱼，仍标记原商品为已重发
        persisted = None

    # 5) 查询新商品记录 ID
    new_goods_id = None
    if persisted:
        result = await db.execute(
            select(XianyuGoods).where(
                and_(
                    XianyuGoods.account_id == account_id,
                    XianyuGoods.external_goods_id == str(new_item_id),
                    XianyuGoods.deleted == 0,
                )
            )
        )
        new_goods = result.scalar_one_or_none()
        if new_goods:
            new_goods_id = new_goods.id

    # 6) 更新原商品：标记为已重发
    await db.execute(
        update(XianyuGoods)
        .where(XianyuGoods.id == goods.id)
        .values(
            next_relist_goods_id=new_goods_id,
            last_relist_at=datetime.now(),
            status=2,  # 已售
        )
    )

    # 7) 更新新商品：标记来源、继承开关、设置快照与原始库存
    if new_goods_id:
        await db.execute(
            update(XianyuGoods)
            .where(XianyuGoods.id == new_goods_id)
            .values(
                relist_source_goods_id=goods.id,
                auto_relist_enabled=1,  # 继承开关，支持链式重发
                has_snapshot=1,
                original_quantity=1,
            )
        )
        # 保存新商品的快照（链式重发支持）
        await _save_relist_snapshot(
            db,
            account_id=account_id,
            external_goods_id=str(new_item_id),
            snapshot_data=snapshot,  # 复用原快照数据
            is_fish_shop=is_fish_shop,
        )

    logger.info(
        "重发成功: 原商品 goods_id=%s external_goods_id=%s → 新商品 external_goods_id=%s new_goods_id=%s",
        goods.id, external_goods_id, new_item_id, new_goods_id,
    )

    return {
        "success": True,
        "itemId": new_item_id,
        "itemUrl": publish_result.get("itemUrl", ""),
        "new_goods_id": new_goods_id,
        "message": "重发成功",
        "goods_id": goods.id,
    }


async def relist_sold_item(
    account_id: int,
    external_goods_id: str,
) -> dict:
    """订单同步钩子调用：商品被卖出后立即触发重发。

    本函数为异步非阻塞，调用方无需等待结果。
    本身创建独立的事务会话，不影响调用方事务。
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                select(XianyuGoods).where(
                    and_(
                        XianyuGoods.account_id == account_id,
                        XianyuGoods.external_goods_id == str(external_goods_id),
                        XianyuGoods.deleted == 0,
                    )
                )
            )
            goods = result.scalar_one_or_none()
            if not goods:
                return {"success": False, "message": "商品不存在"}

            # 检查是否满足重发条件
            if not goods.auto_relist_enabled:
                return {"success": False, "message": "未开启售整自动上架"}
            if not goods.has_snapshot:
                return {"success": False, "message": "无发布快照"}
            if goods.next_relist_goods_id is not None:
                return {"success": False, "message": "已重发过，跳过"}
            if goods.original_quantity != 1:
                return {"success": False, "message": f"非售整场景（original_quantity={goods.original_quantity}）"}

            result = await _do_relist_one(db, goods)
            if result.get("success"):
                await db.commit()
            else:
                await db.rollback()
            return result
    except Exception as e:
        logger.exception(
            "订单钩子触发重发失败 account_id=%s external_goods_id=%s: %s",
            account_id, external_goods_id, e,
        )
        return {"success": False, "message": f"重发异常: {str(e)[:200]}"}


async def manual_relist(
    account_id: int,
    external_goods_id: str,
) -> dict:
    """手动触发重发（/api/item/republish 路由调用）。

    与 relist_sold_item 不同，手动触发放宽条件：
    - 不要求 original_quantity == 1
    - 不要求 status in (0, 2)
    但仍要求 auto_relist_enabled=1 和 has_snapshot=1
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                select(XianyuGoods).where(
                    and_(
                        XianyuGoods.account_id == account_id,
                        XianyuGoods.external_goods_id == str(external_goods_id),
                        XianyuGoods.deleted == 0,
                    )
                )
            )
            goods = result.scalar_one_or_none()
            if not goods:
                return {"success": False, "message": "商品不存在"}

            if not goods.auto_relist_enabled:
                return {"success": False, "message": "未开启售整自动上架，无法手动重发"}
            if not goods.has_snapshot:
                return {"success": False, "message": "无发布快照，无法重发"}
            if goods.next_relist_goods_id is not None:
                return {"success": False, "message": "已重发过，如需再次重发请重置 next_relist_goods_id"}

            result = await _do_relist_one(db, goods)
            if result.get("success"):
                await db.commit()
            else:
                await db.rollback()
            return result
    except Exception as e:
        logger.exception(
            "手动重发失败 account_id=%s external_goods_id=%s: %s",
            account_id, external_goods_id, e,
        )
        return {"success": False, "message": f"重发异常: {str(e)[:200]}"}


async def scan_and_relist() -> dict:
    """定时扫描并重发符合条件的商品（由 relist_scheduler 调用）。

    返回: {"scanned_accounts": int, "total_relisted": int, "total_failed": int, "details": [...]}
    """
    total_relisted = 0
    total_failed = 0
    scanned_accounts = 0
    details: list = []

    try:
        # 1) 查找所有需要重发的账号（distinct account_id）
        async with async_session() as db:
            result = await db.execute(
                select(XianyuGoods.account_id)
                .where(
                    and_(
                        XianyuGoods.auto_relist_enabled == 1,
                        XianyuGoods.has_snapshot == 1,
                        XianyuGoods.status.in_([0, 2]),
                        XianyuGoods.next_relist_goods_id.is_(None),
                        XianyuGoods.original_quantity == 1,
                        XianyuGoods.deleted == 0,
                    )
                )
                .group_by(XianyuGoods.account_id)
                .limit(MAX_ACCOUNTS_PER_SCAN)
            )
            accounts = result.all()

        if not accounts:
            return {
                "scanned_accounts": 0,
                "total_relisted": 0,
                "total_failed": 0,
                "details": [],
            }

        for (account_id,) in accounts:
            scanned_accounts += 1
            try:
                # 每个账号独立事务
                async with async_session() as db:
                    candidates = await _find_relist_candidates(
                        db,
                        account_id=account_id,
                        limit=MAX_RELIST_PER_ACCOUNT_PER_SCAN,
                    )
                    if not candidates:
                        continue

                    for goods in candidates:
                        result = await _do_relist_one(db, goods)
                        if result.get("success"):
                            total_relisted += 1
                            details.append({
                                "account_id": account_id,
                                "external_goods_id": str(goods.external_goods_id),
                                "new_item_id": result.get("itemId"),
                                "success": True,
                            })
                        else:
                            total_failed += 1
                            details.append({
                                "account_id": account_id,
                                "external_goods_id": str(goods.external_goods_id),
                                "success": False,
                                "message": result.get("message"),
                            })

                    # 单账号统一提交
                    await db.commit()
            except Exception as e:
                logger.warning(
                    "扫描重发账号失败 account_id=%s err=%s",
                    account_id, str(e)[:200],
                )
                total_failed += 1

    except Exception as e:
        logger.exception("扫描重发任务异常: %s", e)

    logger.info(
        "扫描重发完成: scanned_accounts=%d, relisted=%d, failed=%d",
        scanned_accounts, total_relisted, total_failed,
    )
    return {
        "scanned_accounts": scanned_accounts,
        "total_relisted": total_relisted,
        "total_failed": total_failed,
        "details": details,
    }
