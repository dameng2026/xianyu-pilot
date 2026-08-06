"""
鱼小铺多规格商品发布/编辑/详情路由（开源版）。

参考商业版 automation-service/app/api/v1/routes/fish_shop.py，
适配开源版单管理员架构（移除 tenant_id 维度，SKU 表按 xianyu_goods.id 关联）。

路由：
- POST /api/fish-shop/publish  鱼小铺多规格发布
- POST /api/fish-shop/edit     鱼小铺多规格编辑
- POST /api/fish-shop/detail   获取完整商品详情（用于编辑回显）

后端权限校验：
- 调用前判断 fish_shop_user=1
- 编辑/详情场景校验商品归属于当前账号
- 不接受前端传入任意 Cookie
"""
from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.cookie_crypto import decrypt_cookie_if_needed
from ....core.database import get_db
from ....core.response import ResultObject
from ....models.entities import (
    XianyuAccount,
    XianyuAccountAuth,
    XianyuGoods,
    XianyuGoodsEditSnapshot,
    XianyuGoodsProperty,
    XianyuGoodsPropertyValue,
    XianyuGoodsSku,
)
from ....services.fish_shop_publish import (
    FISH_SHOP_EDIT_API,
    FISH_SHOP_EDIT_VERSION,
    FISH_SHOP_PUBLISH_API,
    FISH_SHOP_PUBLISH_VERSION,
    build_internal_item_object,
    build_property_key,
    call_fish_shop_api,
    extract_response_item_id,
    extract_response_skus,
    fetch_fish_shop_edit_detail,
    invalidate_edit_detail_cache,
    match_response_skus,
    validate_multi_spec_payload,
)
from ....services.xianyu_goods_sync import (
    XianyuItemPublisher,
    extract_token_from_cookie,
)
from .internal import verify_internal_or_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fish-shop")


# ============================================================
# 共用辅助
# ============================================================

async def _get_account_auth_and_check_fish_shop(
    db: AsyncSession, account_id: int
) -> tuple[Optional[XianyuAccountAuth], bool]:
    """
    获取账号 auth 并校验是否为鱼小铺账号。
    返回 (auth, is_fish_shop)。
    """
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
        return None, False
    is_fish_shop = bool(account.fish_shop_user)
    if not is_fish_shop:
        return None, False

    result = await db.execute(
        select(XianyuAccountAuth).where(
            XianyuAccountAuth.account_id == account_id,
        )
    )
    auth = result.scalar_one_or_none()
    if not auth or not auth.encrypted_cookie:
        return None, True

    return auth, True


async def _verify_goods_belongs_to_account(
    db: AsyncSession, account_id: int, external_goods_id: str
) -> Optional[XianyuGoods]:
    """校验商品归属于当前账号。返回 XianyuGoods 实体，None 表示不存在或不归属。"""
    result = await db.execute(
        select(XianyuGoods).where(
            and_(
                XianyuGoods.account_id == account_id,
                XianyuGoods.external_goods_id == str(external_goods_id),
                XianyuGoods.deleted == 0,
            )
        )
    )
    return result.scalar_one_or_none()


async def _find_or_create_goods(
    db: AsyncSession,
    account_id: int,
    external_goods_id: str,
    *,
    title: Optional[str] = None,
    image_urls: Optional[list] = None,
    price_yuan: Optional[Decimal] = None,
    stock: Optional[int] = None,
) -> XianyuGoods:
    """按闲鱼 itemId 查找本地商品；不存在则创建（供 SKU 表按 goods_id 关联）。"""
    result = await db.execute(
        select(XianyuGoods).where(
            and_(
                XianyuGoods.account_id == account_id,
                XianyuGoods.external_goods_id == str(external_goods_id),
            )
        )
    )
    goods = result.scalar_one_or_none()
    if goods is not None:
        goods.deleted = 0
        if title is not None:
            goods.title = title
        if price_yuan is not None:
            goods.price = str(price_yuan)
            goods.sold_price = str(price_yuan)
        if stock is not None:
            goods.stock = stock
            goods.quantity = stock
        await db.flush()
        return goods
    goods = XianyuGoods(
        account_id=account_id,
        external_goods_id=str(external_goods_id),
        title=title or "",
        price=str(price_yuan) if price_yuan is not None else None,
        sold_price=str(price_yuan) if price_yuan is not None else None,
        cover_pic=(image_urls or [None])[0],
        image_url=image_urls[0] if image_urls else None,
        image_urls=image_urls or None,
        stock=stock or 0,
        quantity=stock or 0,
        status=1,
        deleted=0,
    )
    db.add(goods)
    await db.flush()
    return goods


async def _persist_skus_and_properties(
    db: AsyncSession,
    account_id: int,
    goods_id: int,
    property_groups: list,
    sku_list: list,
) -> None:
    """
    将 SKU 与规格数据写入本地表（开源版表结构，按 xianyu_goods.id 关联）。
    幂等：先物理删除旧的，再插入新的（同 goods_id；开源版表无 deleted 列）。
    """
    from sqlalchemy import delete as sql_delete

    # 1) 删除旧数据（规格值 → 规格 → SKU）
    old_props = (
        await db.execute(
            select(XianyuGoodsProperty).where(
                XianyuGoodsProperty.goods_id == goods_id,
            )
        )
    ).scalars().all()
    for prop in old_props:
        await db.execute(
            sql_delete(XianyuGoodsPropertyValue).where(
                XianyuGoodsPropertyValue.property_id == prop.id
            )
        )
    await db.execute(
        sql_delete(XianyuGoodsProperty).where(
            XianyuGoodsProperty.goods_id == goods_id
        )
    )
    await db.execute(
        sql_delete(XianyuGoodsSku).where(
            XianyuGoodsSku.goods_id == goods_id
        )
    )
    await db.flush()

    # 2) 插入规格类型与规格值
    for idx, g in enumerate(property_groups or []):
        name = (g.get("propertyName") or "").strip()
        if not name:
            continue
        prop = XianyuGoodsProperty(
            account_id=account_id,
            goods_id=goods_id,
            property_name=name,
        )
        db.add(prop)
        await db.flush()
        for v in g.get("propertyValues", []) or []:
            if not isinstance(v, dict):
                continue
            val = (v.get("propertyValue") or "").strip()
            if not val:
                continue
            db.add(XianyuGoodsPropertyValue(
                property_id=prop.id,
                value_name=val,
            ))

    # 3) 插入 SKU
    for sku in sku_list:
        prop_list = sku.get("propertyList") or []
        property_key = build_property_key(prop_list)
        try:
            price_cent = int(sku.get("priceInCent", 0))
        except (ValueError, TypeError):
            price_cent = 0
        try:
            qty = int(sku.get("quantity", 0))
        except (ValueError, TypeError):
            qty = 0
        sku_id = str(sku.get("skuId") or "") or property_key or "-"
        db.add(XianyuGoodsSku(
            account_id=account_id,
            goods_id=goods_id,
            sku_id=sku_id,
            property_key=property_key,
            # 开源版 xianyu_goods_sku.property_list_json 为 Text 列，需显式 JSON 序列化
            property_list_json=json.dumps(prop_list, ensure_ascii=False) if prop_list else None,
            price=Decimal(price_cent) / Decimal(100),
            stock=qty,
        ))
    await db.flush()


def _build_publish_snapshot(req: dict, internal_obj: dict, matched_skus: list) -> dict:
    """构建发布/编辑快照（扁平字段 + 内部对象，供回显与售整重发使用）。

    扁平字段与 relist_service 的读取约定一致：
    title/description/imageUrls/price/shippingMode/supportSelfPick/postFee/location/category/
    itemSkuList/itemProperties/origPrice。
    """
    min_price_cent = min(
        (int(s.get("priceInCent", 0)) for s in matched_skus),
        default=0,
    )
    snapshot = {
        "title": (req.get("title") or "").strip(),
        "description": (req.get("description") or "").strip(),
        "imageUrls": req.get("imageUrls", []) or [],
        "price": str(Decimal(min_price_cent) / Decimal(100)) if min_price_cent else "",
        "shippingMode": req.get("shippingMode", "free"),
        "supportSelfPick": req.get("supportSelfPick", False),
        "location": req.get("location", {}),
        "category": req.get("category", {}),
    }
    if req.get("origPrice"):
        snapshot["origPrice"] = req.get("origPrice")
    if req.get("postFee"):
        snapshot["postFee"] = req.get("postFee")
    if req.get("itemSkuList"):
        snapshot["itemSkuList"] = req.get("itemSkuList")
    if req.get("itemProperties"):
        snapshot["itemProperties"] = req.get("itemProperties")
    # 保留内部对象与响应 SKU，供编辑回显兜底
    snapshot["internalItem"] = internal_obj
    snapshot["responseSkus"] = matched_skus
    return snapshot


async def _save_edit_snapshot(
    db: AsyncSession,
    account_id: int,
    external_goods_id: str,
    snapshot: dict,
    source: str,
) -> None:
    """保存编辑快照（编辑回显兜底 + 售整自动上架重发）。"""
    db.add(XianyuGoodsEditSnapshot(
        account_id=account_id,
        external_goods_id=str(external_goods_id),
        snapshot_json=snapshot,
        source=source,
    ))
    await db.flush()


async def _sync_category_recommend(
    publisher: XianyuItemPublisher, title: str, desc: str, image_urls: list
) -> dict:
    """同步调用类目推荐，包装为异步。"""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            None, publisher.category_recommend, title, desc, image_urls
        )
    except Exception as exc:
        logger.warning("category_recommend_failed err=%s", str(exc)[:200])
        return {"recommended": False}


def _category_info_from_request(req: dict, publisher: XianyuItemPublisher) -> dict:
    user_cat = req.get("category", {}) or {}
    return {
        "recommended": False,
        "catId": user_cat.get("catId") or publisher.DEFAULT_CAT_ID,
        "catName": user_cat.get("catName") or publisher.DEFAULT_CAT_NAME,
        "channelCatId": user_cat.get("channelCatId") or publisher.DEFAULT_CHANNEL_CAT_ID,
        "tbCatId": user_cat.get("tbCatId") or publisher.DEFAULT_TB_CAT_ID,
        "cardList": [],
    }


# ============================================================
# 路由：发布
# ============================================================

@router.post("/publish")
async def publish_fish_shop_item(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_or_current_user),
):
    """
    鱼小铺多规格商品发布。

    请求体：
    {
        xianyuAccountId: int,
        title, description, imageUrls,
        itemProperties: [{propertyName, supportImage, propertyValues: [{propertyValue, propertyValueImg}]}],
        itemSkuList: [{price, quantity, propertyList: [{propertyText, valueText}]}],
        shippingMode, supportSelfPick, postFee, location, category
    }
    """
    try:
        account_id = req.get("xianyuAccountId") or req.get("xianyu_account_id")
        if not account_id:
            return ResultObject.failed("缺少参数 xianyuAccountId")
        account_id = int(account_id)

        # 1) 后端权限校验：必须是鱼小铺账号
        auth, is_fish_shop = await _get_account_auth_and_check_fish_shop(db, account_id)
        if not is_fish_shop:
            return ResultObject.failed("当前闲鱼账号不是鱼小铺，无法发布多规格商品", code=403)
        if not auth:
            return ResultObject.failed("账号未登录或 Cookie 已失效，请重新登录")

        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)
        token = extract_token_from_cookie(cookie_str)
        if not token:
            return ResultObject.failed("Cookie 中缺少 _m_h5_tk，请重新登录")

        # 2) 参数校验
        title = (req.get("title") or "").strip()
        if not title:
            return ResultObject.failed("宝贝标题不能为空")
        if len(title) > 30:
            return ResultObject.failed("宝贝标题不能超过30个字")

        description = (req.get("description") or "").strip()
        if not description:
            return ResultObject.failed("宝贝描述不能为空")

        image_urls = req.get("imageUrls", []) or []
        if not image_urls:
            return ResultObject.failed("请至少上传一张商品图片")

        property_groups = req.get("itemProperties", []) or []
        sku_list = req.get("itemSkuList", []) or []
        if not property_groups or not sku_list:
            return ResultObject.failed("多规格商品必须包含规格类型和 SKU")

        # 3) 多规格校验
        validation_error = validate_multi_spec_payload(req)
        if validation_error:
            return ResultObject.failed(validation_error)

        # 4) 类目推荐（复用现有能力）
        publisher = XianyuItemPublisher(cookie_str)
        recommend_result = await _sync_category_recommend(publisher, title, description, image_urls)
        category_info = recommend_result if recommend_result.get("recommended") else _category_info_from_request(req, publisher)

        # 5) 图片上传到闲鱼 CDN（复用现有能力）
        #    编辑/回显场景下闲鱼 CDN 图片可能为 http://，统一归一化为 https:// 再上传
        #    （download_public_image 仅允许公网 HTTPS 图片）
        upload_sources = [
            u.replace("http://", "https://", 1) if str(u).startswith("http://") else u
            for u in image_urls
        ]
        # 放线程池执行：upload_image_to_xianyu 内部使用 asyncio.run() 下载，不能在事件循环中直接调用
        xianyu_image_urls = await asyncio.to_thread(publisher.upload_images_to_xianyu, upload_sources)
        if not xianyu_image_urls:
            return ResultObject.failed("图片上传到闲鱼失败，请重试")

        # 6) 构造内部商品对象（发布场景，不带 itemId）
        internal_obj = build_internal_item_object(
            req, xianyu_image_urls, category_info, is_edit=False
        )

        # 7) 调用鱼小铺 publish API（双层序列化在 call_fish_shop_api 内完成）
        result = call_fish_shop_api(
            cookie_str, FISH_SHOP_PUBLISH_API, FISH_SHOP_PUBLISH_VERSION,
            internal_obj, is_edit=False,
        )

        # 8) 提取响应 itemId 与 SKU
        new_item_id = extract_response_item_id(result)
        if not new_item_id:
            return ResultObject.failed("发布成功但未返回 itemId，请稍后到商品列表同步")

        response_skus = extract_response_skus(result)
        matched_skus = match_response_skus(response_skus, internal_obj["itemSkuList"])

        # 9) 本地商品记录 + SKU 持久化（按 goods_id 关联）
        total_qty = sum(int(s.get("quantity", 0)) for s in matched_skus)
        min_price_cent = min(
            (int(s.get("priceInCent", 0)) for s in matched_skus),
            default=0,
        )
        price_yuan = Decimal(min_price_cent) / Decimal(100)
        goods = await _find_or_create_goods(
            db, account_id, new_item_id,
            title=title,
            image_urls=image_urls,
            price_yuan=price_yuan,
            stock=total_qty,
        )
        await _persist_skus_and_properties(
            db, account_id, goods.id,
            internal_obj["itemProperties"], matched_skus,
        )
        # 保存编辑快照 + 标记售整前置条件（快照是售整自动上架的前提）
        await _save_edit_snapshot(
            db, account_id, new_item_id,
            _build_publish_snapshot(req, internal_obj, matched_skus),
            source="publish",
        )
        goods.has_snapshot = 1
        if total_qty == 1:
            goods.original_quantity = 1
        await db.commit()

        return ResultObject.success({
            "itemId": new_item_id,
            "goodsId": goods.id,
            "skuList": matched_skus,
            "totalQuantity": str(total_qty),
            "minPriceInCent": str(min_price_cent),
        })
    except Exception as exc:
        logger.warning("fish_shop_publish_failed err=%s", str(exc)[:200])
        await db.rollback()
        return ResultObject.failed(f"鱼小铺多规格发布失败，请稍后重试: {type(exc).__name__}")


# ============================================================
# 路由：编辑
# ============================================================

@router.post("/edit")
async def edit_fish_shop_item(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_or_current_user),
):
    """
    鱼小铺多规格商品编辑。

    请求体：
    {
        xianyuAccountId: int,
        itemId: str,  # 必填，目标闲鱼商品 itemId
        title, description, imageUrls, itemProperties, itemSkuList,
        shippingMode, supportSelfPick, postFee, location, category
    }
    """
    item_id = ""
    try:
        account_id = int(req.get("xianyuAccountId") or req.get("xianyu_account_id") or 0)
        if not account_id:
            return ResultObject.failed("缺少参数 xianyuAccountId")

        item_id = (req.get("itemId") or "").strip()
        if not item_id:
            return ResultObject.failed("编辑请求必须携带 itemId")

        # 1) 后端权限校验：必须是鱼小铺账号
        auth, is_fish_shop = await _get_account_auth_and_check_fish_shop(db, account_id)
        if not is_fish_shop:
            return ResultObject.failed("当前闲鱼账号不是鱼小铺，无法编辑多规格商品", code=403)
        if not auth:
            return ResultObject.failed("账号未登录或 Cookie 已失效，请重新登录")

        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)
        token = extract_token_from_cookie(cookie_str)
        if not token:
            return ResultObject.failed("Cookie 中缺少 _m_h5_tk，请重新登录")

        # 2) 校验商品归属
        goods = await _verify_goods_belongs_to_account(db, account_id, item_id)
        if not goods:
            return ResultObject.failed("商品不存在或不归属当前账号", code=403)

        # 3) 参数校验
        title = (req.get("title") or "").strip()
        if not title:
            return ResultObject.failed("宝贝标题不能为空")

        description = (req.get("description") or "").strip()
        if not description:
            return ResultObject.failed("宝贝描述不能为空")

        image_urls = req.get("imageUrls", []) or []
        if not image_urls:
            return ResultObject.failed("请至少上传一张商品图片")

        property_groups = req.get("itemProperties", []) or []
        sku_list = req.get("itemSkuList", []) or []
        if not property_groups or not sku_list:
            return ResultObject.failed("多规格商品必须包含规格类型和 SKU")

        validation_error = validate_multi_spec_payload(req)
        if validation_error:
            return ResultObject.failed(validation_error)

        # 4) 类目推荐
        publisher = XianyuItemPublisher(cookie_str)
        recommend_result = await _sync_category_recommend(publisher, title, description, image_urls)
        category_info = recommend_result if recommend_result.get("recommended") else _category_info_from_request(req, publisher)

        # 5) 图片上传（http 图片 URL 归一化为 https，download_public_image 仅允许公网 HTTPS）
        upload_sources = [
            u.replace("http://", "https://", 1) if str(u).startswith("http://") else u
            for u in image_urls
        ]
        # 放线程池执行：upload_image_to_xianyu 内部使用 asyncio.run() 下载，不能在事件循环中直接调用
        xianyu_image_urls = await asyncio.to_thread(publisher.upload_images_to_xianyu, upload_sources)
        if not xianyu_image_urls:
            return ResultObject.failed("图片上传到闲鱼失败，请重试")

        # 6) 构造内部对象（编辑场景，携带 itemId）
        internal_obj = build_internal_item_object(
            req, xianyu_image_urls, category_info, is_edit=True
        )

        # 7) 调用 edit API
        result = call_fish_shop_api(
            cookie_str, FISH_SHOP_EDIT_API, FISH_SHOP_EDIT_VERSION,
            internal_obj, is_edit=True,
        )

        # 8) 校验响应 itemId 与目标 itemId 一致
        response_item_id = extract_response_item_id(result)
        if response_item_id and response_item_id != str(item_id):
            logger.warning("fish_shop_edit_item_id_mismatch expected=%s got=%s", item_id, response_item_id)
            return ResultObject.failed("编辑响应的 itemId 与目标不一致，已拒绝写入")

        # 9) 匹配响应 SKU
        response_skus = extract_response_skus(result)
        matched_skus = match_response_skus(response_skus, internal_obj["itemSkuList"])

        # 10) 持久化（本地商品 + SKU/规格）
        total_qty = sum(int(s.get("quantity", 0)) for s in matched_skus)
        min_price_cent = min(
            (int(s.get("priceInCent", 0)) for s in matched_skus),
            default=0,
        )
        price_yuan = Decimal(min_price_cent) / Decimal(100)
        goods.title = title
        goods.price = str(price_yuan.quantize(Decimal("0.01")))
        goods.sold_price = str(price_yuan.quantize(Decimal("0.01")))
        goods.stock = total_qty
        goods.quantity = total_qty
        await db.flush()
        await _persist_skus_and_properties(
            db, account_id, goods.id,
            internal_obj["itemProperties"], matched_skus,
        )
        # 保存编辑快照 + 标记售整前置条件
        await _save_edit_snapshot(
            db, account_id, str(item_id),
            _build_publish_snapshot(req, internal_obj, matched_skus),
            source="edit",
        )
        goods.has_snapshot = 1
        if total_qty == 1:
            goods.original_quantity = 1
        await db.commit()

        # 11) 失效 editdetail 缓存，避免编辑成功后仍展示陈旧数据
        try:
            invalidate_edit_detail_cache(account_id, str(item_id))
        except Exception:
            pass

        return ResultObject.success({
            "itemId": str(item_id),
            "goodsId": goods.id,
            "skuList": matched_skus,
            "totalQuantity": str(total_qty),
            "minPriceInCent": str(min_price_cent),
        })
    except Exception as exc:
        logger.exception("fish_shop_edit_failed item_id=%s", item_id)
        await db.rollback()
        return ResultObject.failed(f"鱼小铺多规格编辑失败，请稍后重试: {type(exc).__name__}")


# ============================================================
# 路由：详情（用于编辑回显）
# ============================================================

@router.post("/detail")
async def get_fish_shop_detail(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_or_current_user),
):
    """
    获取完整商品详情，用于编辑回显。

    请求体：
    {
        xianyuAccountId: int,
        itemId: str,
        bypassCache: bool  # 可选，True 时强制刷新缓存
    }

    优先级：
    1. 调用闲鱼 editdetail 接口获取完整编辑详情
    2. 本地 SKU/规格表（补充/兜底）
    3. 本地 xianyu_goods 简略数据（兜底）
    """
    item_id = ""
    try:
        account_id = int(req.get("xianyuAccountId") or req.get("xianyu_account_id") or 0)
        if not account_id:
            return ResultObject.failed("缺少参数 xianyuAccountId")

        item_id = (req.get("itemId") or "").strip()
        if not item_id:
            return ResultObject.failed("缺少参数 itemId")

        # 1) 后端权限校验：必须是鱼小铺账号
        auth, is_fish_shop = await _get_account_auth_and_check_fish_shop(db, account_id)
        if not is_fish_shop:
            return ResultObject.failed("当前闲鱼账号不是鱼小铺，无法查看编辑详情", code=403)
        if not auth:
            return ResultObject.failed("账号未登录或 Cookie 已失效，请重新登录")

        # 2) 校验商品归属
        goods = await _verify_goods_belongs_to_account(db, account_id, item_id)
        if not goods:
            return ResultObject.failed("商品不存在或不归属当前账号", code=403)

        # 3) 调用闲鱼 editdetail 接口获取完整编辑详情
        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)
        bypass_cache = bool(req.get("bypassCache") or req.get("bypass_cache") or False)

        loop = asyncio.get_event_loop()
        try:
            edit_detail = await loop.run_in_executor(
                None,
                lambda: fetch_fish_shop_edit_detail(
                    cookie_str=cookie_str,
                    account_id=account_id,
                    item_id=str(item_id),
                    bypass_cache=bypass_cache,
                )
            )
        except Exception as api_exc:
            logger.warning(
                "fish_shop_detail_api_failed item_id=%s err=%s",
                str(item_id)[:32], str(api_exc)[:200],
            )
            edit_detail = None

        # 4) 读取本地规格/SKU（作为补充/兜底）
        props = (
            await db.execute(
                select(XianyuGoodsProperty).where(
                    XianyuGoodsProperty.goods_id == goods.id,
                )
            )
        ).scalars().all()
        item_properties: list = []
        for p in props:
            values = (
                await db.execute(
                    select(XianyuGoodsPropertyValue).where(
                        XianyuGoodsPropertyValue.property_id == p.id,
                    )
                )
            ).scalars().all()
            item_properties.append({
                "propertyName": p.property_name,
                "supportImage": False,
                "propertyValues": [
                    {"propertyValue": v.value_name, "propertyValueImg": ""}
                    for v in values
                ],
            })

        skus = (
            await db.execute(
                select(XianyuGoodsSku).where(
                    XianyuGoodsSku.goods_id == goods.id,
                )
            )
        ).scalars().all()
        item_sku_list: list = []
        for s in skus:
            item_sku_list.append({
                "priceInCent": str(int((s.price or 0) * 100)),
                "quantity": str(s.stock or 0),
                "propertyList": s.property_list_json or [],
                "skuId": s.sku_id or "",
                "inventoryId": "",
            })

        # 5) 优先使用 editdetail 接口返回的完整数据，本地数据作为补充
        if edit_detail is not None:
            detail = {
                "itemId": edit_detail.get("itemId", str(item_id)),
                "itemStatus": edit_detail.get("itemStatus", ""),
                "itemTypeStr": edit_detail.get("itemTypeStr", ""),
                "simpleItem": edit_detail.get("simpleItem", False),
                "title": edit_detail.get("title", ""),
                "description": edit_detail.get("description", ""),
                "wlDescription": edit_detail.get("wlDescription", ""),
                "imageUrls": edit_detail.get("imageUrls", []),
                "majorImageUrl": edit_detail.get("majorImageUrl", ""),
                "imageList": edit_detail.get("imageList", []),
                "priceInCent": edit_detail.get("priceInCent", 0),
                "quantity": edit_detail.get("quantity", 0),
                "catId": edit_detail.get("catId", ""),
                "catName": edit_detail.get("catName", ""),
                "tbCatId": edit_detail.get("tbCatId", ""),
                "channelCatId": edit_detail.get("channelCatId", ""),
                "itemLabelExtList": edit_detail.get("itemLabelExtList", []),
                "prov": edit_detail.get("prov", ""),
                "city": edit_detail.get("city", ""),
                "area": edit_detail.get("area", ""),
                "poiName": edit_detail.get("poiName", ""),
                "divisionId": edit_detail.get("divisionId", ""),
                "gps": edit_detail.get("gps", ""),
                "poiId": edit_detail.get("poiId", ""),
                "canFreeShipping": edit_detail.get("canFreeShipping", False),
                "onlyTakeSelf": edit_detail.get("onlyTakeSelf", False),
                "supportFreight": edit_detail.get("supportFreight", False),
                "idleTemplateId": edit_detail.get("idleTemplateId", ""),
                "templateId": edit_detail.get("templateId", ""),
                "postPriceInCent": edit_detail.get("postPriceInCent", 0),
                "userRightsProtocols": edit_detail.get("userRightsProtocols", []),
                "itemProperties": edit_detail.get("itemProperties", []) or item_properties,
                "itemSkuList": edit_detail.get("itemSkuList", []) or item_sku_list,
                "propertyImageList": edit_detail.get("propertyImageList", []),
                "isMultiSpec": edit_detail.get("isMultiSpec", False),
                "snapshot": None,
                "source": "editdetail",
            }
        else:
            # editdetail 接口失败：降级到本地数据
            detail = {
                "itemId": str(item_id),
                "title": goods.title or "",
                "description": goods.detail_info or goods.description or "",
                "imageUrls": (goods.image_url or "").split(",") if goods.image_url else [],
                "price": goods.price or "",
                "stock": goods.stock or "",
                "itemProperties": item_properties,
                "itemSkuList": item_sku_list,
                "snapshot": None,
                "source": "local_fallback",
                "warning": "未能从闲鱼获取最新编辑详情，当前展示为本地缓存数据",
            }

        return ResultObject.success(detail)
    except Exception as exc:
        logger.warning("fish_shop_detail_failed item_id=%s err=%s", item_id, str(exc)[:200])
        await db.rollback()
        return ResultObject.failed(f"获取鱼小铺商品详情失败，请稍后重试: {type(exc).__name__}")
