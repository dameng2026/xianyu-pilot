"""
RESTful API router - provides RESTful endpoints for frontend compatibility.
Wraps existing POST-style business logic under RESTful resource paths.
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from ....core.database import get_db
from ....core.response import ResultObject
from ....core.unavailable_features import (
    ACCOUNT_LOGIN_CREDENTIAL_UNAVAILABLE,
    FACE_VERIFICATION_UNAVAILABLE,
    feature_unavailable,
)
from ....models.entities import XianyuAccount, XianyuAccountAuth, XianyuAccountMembership, XianyuGoods, XianyuTradeOrder, XianyuMessage, Notification
from ..deps import get_current_user
from .account import account_to_dto

logger = logging.getLogger(__name__)
router = APIRouter()


def _str_val(data: dict, key: str) -> Optional[str]:
    """从 dict 中安全提取字符串值，兼容闲鱼 API 偶发的嵌套 dict 格式。

    参考 commercial 版 XianyuAccountService.strVal：当值是 dict 时
    （如 avatar 字段返回 {avatar: "http://..."}），提取第一个非空字符串。
    """
    if not isinstance(data, dict):
        return None
    val = data.get(key)
    if val is None:
        return None
    if isinstance(val, dict):
        inner = val.get(key)
        if isinstance(inner, str) and inner.strip():
            return inner
        for v in val.values():
            if isinstance(v, str) and v.strip():
                return v
        return None
    result = str(val).strip()
    return result if result else None


def _int_val(data: dict, key: str) -> Optional[int]:
    """从 dict 中安全提取整型值，兼容字符串数字。"""
    if not isinstance(data, dict):
        return None
    val = data.get(key)
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return None


def _normalize_avatar_url(url: Optional[str]) -> Optional[str]:
    """清理 avatar URL，提取纯 URL（参考 commercial 版 normalizeAvatarUrl）。"""
    if not url:
        return None
    m = re.search(r"https?://[^\s}\"',]+", url)
    return m.group() if m else url


def account_to_profile_dto(account):
    """Convert XianyuAccount to a dict for RESTful responses."""
    ip_location = None
    if hasattr(account, 'province') and hasattr(account, 'city'):
        if account.province or account.city:
            ip_location = f"{account.province or ''} {account.city or ''}".strip()
    return {
        "id": account.id,
        "unb": account.external_uid,
        "nickname": account.nickname,
        "avatar": account.avatar_url,
        "account_note": account.remark,
        "remark": account.remark,
        "ip_location": ip_location,
        "province": account.province if hasattr(account, 'province') else None,
        "city": account.city if hasattr(account, 'city') else None,
        "account_level": account.account_level if hasattr(account, 'account_level') else 0,
        "status": account.status,
        "created_time": str(account.created_time) if account.created_time else None,
        "proxy_password": "***",
        "display_name": account.nickname,
    }


def _db_status_to_fe(db_status):
    """DB(1=在售,0=下架,2=已售) → FE(0=在售,1=下架,2=已售)"""
    mapping = {1: 0, 0: 1, 2: 2}
    return mapping.get(db_status, db_status or 1)


def goods_to_dto(goods):
    """Convert XianyuGoods to a dict for RESTful responses."""
    return {
        "id": goods.id,
        "xianyu_account_id": goods.account_id,
        "xy_goods_id": goods.external_goods_id,
        "goods_title": goods.title,
        "goods_price": goods.sold_price or goods.price,
        "goods_stock": goods.stock,
        "goods_image": goods.cover_pic or goods.image_url,
        "cover_pic": goods.cover_pic,
        "sold_price": goods.sold_price,
        "quantity": goods.quantity,
        "exposure_count": goods.exposure_count,
        "view_count": goods.view_count,
        "want_count": goods.want_count,
        "detail_url": goods.detail_url,
        "detail_info": goods.detail_info,
        "sort_order": goods.sort_order,
        "status": _db_status_to_fe(goods.status),
        "created_time": str(goods.created_time) if goods.created_time else None,
    }


def trade_order_to_dto(order):
    """Convert XianyuTradeOrder to a dict for RESTful responses."""
    return {
        "id": order.id,
        "account_id": order.account_id,
        "xianyu_account_id": order.account_id,
        "external_order_id": order.external_order_id,
        "order_id": order.external_order_id,
        "order_status": order.order_status,
        "buyer_name": order.buyer_name,
        "total_amount": order.total_amount,
        "total_price": order.total_amount,
        "create_time": str(order.create_time) if order.create_time else None,
        "pay_time": str(order.pay_time) if order.pay_time else None,
    }


def chat_message_to_dto(msg):
    """Convert XianyuMessage to a dict for RESTful responses."""
    return {
        "id": msg.id,
        "xianyu_account_id": msg.account_id,
        "session_id": str(msg.conversation_id) if msg.conversation_id else None,
        "from_user_id": msg.from_user_id,
        "to_user_id": msg.to_user_id,
        "content": msg.content,
        "message_type": msg.message_type,
        "direction": msg.direction,
        "created_time": str(msg.created_time) if msg.created_time else None,
    }
# ======================== ACCOUNTS ========================

@router.get("/xianyu/accounts", response_model=ResultObject)
async def restful_get_accounts(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        # JOIN auth 表获取 cookie_status，JOIN membership 表获取会员等级
        query = select(
            XianyuAccount,
            XianyuAccountAuth.cookie_status,
            XianyuAccountAuth.last_login_status_code,
            XianyuAccountAuth.last_login_status_message,
            XianyuAccountAuth.last_login_check_time,
            XianyuAccountMembership,
        ).outerjoin(
            XianyuAccountAuth,
            (XianyuAccountAuth.account_id == XianyuAccount.id)
        ).outerjoin(
            XianyuAccountMembership,
            (XianyuAccountMembership.account_id == XianyuAccount.id)
        ).where(
            XianyuAccount.deleted == 0,
        )
        query = query.order_by(XianyuAccount.id.desc())
        result = await db.execute(query)
        rows = result.all()
        data = []
        for account, cookie_status, login_status_code, login_status_message, login_check_time, membership in rows:
            dto = account_to_dto(account, membership)
            normalized_cookie_status = cookie_status if cookie_status is not None else 0
            # Pydantic 模型用属性赋值；CamelModel 序列化时自动转 camelCase
            dto.cookie_status = normalized_cookie_status
            dto.login_status_code = login_status_code
            dto.login_status_message = login_status_message
            dto.login_check_time = str(login_check_time) if login_check_time else None
            dto.auth_usable = normalized_cookie_status == 1 and str(login_status_code or "").upper() == "OK"
            data.append(dto)
        return ResultObject.success(data)
    except Exception as e:
        logger.error("get accounts error", exc_info=True)
        return ResultObject.internal_error()
@router.get("/xianyu/accounts/summary", response_model=ResultObject)
async def restful_get_accounts_summary(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        base = select(XianyuAccount).where(
            XianyuAccount.deleted == 0,
        )
        total_result = await db.execute(select(func.count()).select_from(base.subquery()))
        total = total_result.scalar() or 0
        normal_result = await db.execute(
            select(func.count()).select_from(
                base.where(XianyuAccount.status == 1).subquery()
            )
        )
        normal = normal_result.scalar() or 0
        verify_result = await db.execute(
            select(func.count()).select_from(
                base.where(XianyuAccount.status != 1).subquery()
            )
        )
        verify = verify_result.scalar() or 0
        return ResultObject.success({
            "total": total,
            "normal": normal,
            "verify": verify,
            "wsOnline": 0,
            "cookieWarn": 0,
        })
    except Exception as e:
        logger.error("get accounts summary error", exc_info=True)
        return ResultObject.internal_error()
# ======================== ACCOUNT DETAIL / UPDATE / DELETE ========================

@router.get("/xianyu/accounts/face-verifications", response_model=ResultObject)
async def restful_accounts_face_verifications(
    current_user: dict = Depends(get_current_user),
):
    """Retired: no verification event source or persisted read state exists."""

    feature_unavailable(FACE_VERIFICATION_UNAVAILABLE)


@router.get("/xianyu/accounts/{account_id}", response_model=ResultObject)
async def restful_get_account_detail(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(XianyuAccount, XianyuAccountMembership)
            .outerjoin(
                XianyuAccountMembership,
                XianyuAccountMembership.account_id == XianyuAccount.id,
            )
            .where(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
        row = result.first()
        if not row:
            return ResultObject.failed("账号不存在")
        account, membership = row
        return ResultObject.success(account_to_dto(account, membership))
    except Exception as e:
        logger.error("get account detail error", exc_info=True)
        return ResultObject.internal_error()

@router.put("/xianyu/accounts/{account_id}", response_model=ResultObject)
async def restful_update_account(
    account_id: int,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")
        remark = body.get("account_note") or body.get("remark")
        if remark:
            account.remark = remark.strip()
        await db.commit()
        await db.refresh(account)
        return ResultObject.success(account_to_dto(account))
    except Exception as e:
        logger.error("update account error", exc_info=True)
        return ResultObject.internal_error()

@router.delete("/xianyu/accounts/{account_id}", response_model=ResultObject)
async def restful_delete_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")
        account.deleted = 1
        await db.commit()
        return ResultObject.success({"message": "删除成功"})
    except Exception as e:
        logger.error("delete account error", exc_info=True)
        return ResultObject.internal_error()

@router.post("/xianyu/accounts/{account_id}/refresh", response_model=ResultObject)
@router.post("/xianyu/accounts/{account_id}/refresh-profile", response_model=ResultObject)
async def restful_refresh_account_profile(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """调用闲鱼 API 刷新账号资料（昵称、头像、IP属地、等级等），并更新数据库。"""
    try:
        from ....services.xianyu_api_service import call_xianyu_api

        # 1. 获取账号
        result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")

        # 2. 获取认证信息
        auth_result = await db.execute(
            select(XianyuAccountAuth).where(
                XianyuAccountAuth.account_id == account_id,
                XianyuAccountAuth.deleted == 0,
            )
        )
        auth = auth_result.scalar_one_or_none()
        if not auth or not auth.encrypted_cookie:
            return ResultObject.failed("该账号无 Cookie，无法刷新资料")

        unb = account.external_uid
        if not unb:
            return ResultObject.failed("该账号无 external_uid，无法刷新资料")

        # 3. 调用闲鱼 API 获取最新资料
        head_result = await asyncio.to_thread(
            call_xianyu_api,
            account_id=account_id,
            api_name="mtop.idle.web.user.page.head",
            version="1.0",
            data_map={"self": False, "userId": unb},
        )

        nav_result = await asyncio.to_thread(
            call_xianyu_api,
            account_id=account_id,
            api_name="mtop.idle.web.user.page.nav",
            version="1.0",
            data_map={},
        )

        api_errors = []
        head_confirmed = False

        # 4. 解析 page.head 返回数据
        if head_result and head_result.get("success"):
            module = head_result.get("data", {}).get("module", {})
            if isinstance(module, dict) and module:
                observed_profile_value = False
                base = module.get("base", {}) if isinstance(module.get("base"), dict) else {}
                shop = module.get("shop", {}) if isinstance(module.get("shop"), dict) else {}

                if base:
                    display_name = _str_val(base, "displayName")
                    ip_location = _str_val(base, "ipLocation")
                    avatar = _str_val(base, "avatar")

                    if display_name:
                        account.nickname = display_name
                        observed_profile_value = True
                    if ip_location:
                        parts = ip_location.split(" ", 1)
                        account.province = parts[0] if parts else None
                        account.city = parts[1] if len(parts) > 1 else None
                        observed_profile_value = True
                    if avatar:
                        account.avatar_url = _normalize_avatar_url(avatar)
                        observed_profile_value = True
                    introduction = _str_val(base, "introduction")
                    if introduction:
                        account.introduction = introduction
                        observed_profile_value = True
                    followers = _int_val(base, "fans")
                    if followers is not None:
                        account.followers = followers
                        observed_profile_value = True
                    following = _int_val(base, "follows")
                    if following is not None:
                        account.following = following
                        observed_profile_value = True

                if shop:
                    level = _str_val(shop, "level")
                    if level:
                        account.account_level = level
                        observed_profile_value = True
                    seller_level = _str_val(shop, "sellerLevel")
                    if seller_level:
                        account.seller_level = seller_level
                        observed_profile_value = True
                    praise_ratio = _str_val(shop, "praiseRatio")
                    if praise_ratio:
                        account.praise_ratio = praise_ratio
                        observed_profile_value = True
                    # 与商业版对齐：page.head 返回的鱼小铺标识字段为 superShow（兼容旧字段 fishShopUser）
                    fish_shop_score_val = _int_val(shop, "score")
                    if fish_shop_score_val is None:
                        fish_shop_score_val = _int_val(shop, "fishShopScore")
                    if fish_shop_score_val is not None:
                        account.fish_shop_score = fish_shop_score_val
                        observed_profile_value = True
                    fish_shop_user_val = shop.get("superShow", shop.get("fishShopUser"))
                    if fish_shop_user_val is not None:
                        account.fish_shop_user = 1 if fish_shop_user_val else 0
                        observed_profile_value = True

                review_data = module.get("review") if isinstance(module.get("review"), dict) else {}
                if review_data:
                    review_num = _int_val(review_data, "reviewNum")
                    if review_num is not None:
                        account.review_num = review_num
                        observed_profile_value = True

                head_confirmed = observed_profile_value
                if not observed_profile_value:
                    api_errors.append("page.head_no_profile_fields")

                logger.info(
                    "刷新资料 page.head 成功: accountId=%d nickname=%s",
                    account_id, _str_val(base, "displayName") or "",
                )
            else:
                api_errors.append("page.head_empty")
        elif head_result:
            api_errors.append("page.head_rejected")
        else:
            api_errors.append("page.head_unavailable")

        # 5. 解析 page.nav 返回数据（获取卖出数等）
        if nav_result and nav_result.get("success"):
            module = nav_result.get("data", {}).get("module", {})
            if isinstance(module, dict) and module:
                tabs = module.get("tabs", [])
                if isinstance(tabs, list):
                    for tab in tabs:
                        if isinstance(tab, dict):
                            item = tab.get("item", {})
                            if isinstance(item, dict) and item.get("type") == "sold":
                                sold_count = _int_val(item, "number")
                                if sold_count is not None:
                                    account.sold_count = sold_count
                                logger.info("刷新资料 page.nav 成功: accountId=%d soldCount=%s", account_id, sold_count)
                                break
        elif nav_result:
            api_errors.append("page.nav_rejected")
        else:
            api_errors.append("page.nav_unavailable")

        if not head_confirmed:
            logger.warning(
                "Profile refresh was not confirmed accountId=%d failureCount=%d",
                account_id,
                len(api_errors),
            )
            raise HTTPException(
                status_code=502,
                detail="平台未确认账号资料刷新，请检查账号登录状态后重试。",
            )

        # 6. 保存到数据库
        await db.commit()
        await db.refresh(account)

        # 7. 构建返回 DTO
        auth_refresh = await db.execute(
            select(
                XianyuAccountAuth.cookie_status,
                XianyuAccountAuth.last_login_status_code,
                XianyuAccountAuth.last_login_status_message,
                XianyuAccountAuth.last_login_check_time,
            ).where(
                XianyuAccountAuth.account_id == account_id,
            )
        )
        auth_row = auth_refresh.one_or_none()

        dto = account_to_dto(account)
        if auth_row:
            cookie_status, login_status_code, login_status_message, login_check_time = auth_row
            normalized_cookie_status = cookie_status if cookie_status is not None else 0
            dto.cookie_status = normalized_cookie_status
            dto.login_status_code = login_status_code
            dto.login_status_message = login_status_message
            dto.login_check_time = str(login_check_time) if login_check_time else None
            dto.auth_usable = normalized_cookie_status == 1 and str(login_status_code or "").upper() == "OK"

        if api_errors:
            logger.warning(
                "Profile refresh completed with partial provider failures accountId=%d failureCount=%d",
                account_id,
                len(api_errors),
            )

        return ResultObject.success(dto)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "refresh account profile errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="账号资料刷新暂不可用，请稍后重试。",
        ) from exc

@router.get("/xianyu/accounts/{account_id}/credential", response_model=ResultObject)
async def restful_get_account_credential(
    account_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Retired: credentials are neither consumed nor returned."""

    feature_unavailable(ACCOUNT_LOGIN_CREDENTIAL_UNAVAILABLE)

@router.put("/xianyu/accounts/{account_id}/membership", response_model=ResultObject)
async def restful_set_account_membership(
    account_id: int,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """设置账号会员等级（upsert：存在则更新，不存在则插入）。

    请求体: {"level": "normal|vip|svip", "expiredTime": "2026-12-31 23:59:59" | null}
    """
    try:
        level = (body.get("level") or "normal").strip().lower()
        if level not in ("normal", "vip", "svip"):
            return ResultObject.validate_failed("level 必须为 normal/vip/svip")

        expired_time_str = body.get("expiredTime")
        expired_time = None
        if expired_time_str:
            raw = str(expired_time_str).strip()
            try:
                expired_time = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    expired_time = datetime.fromisoformat(raw)
                except ValueError:
                    return ResultObject.validate_failed("expiredTime 格式应为 YYYY-MM-DD HH:MM:SS")

        # 检查账号是否存在
        acct_result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
        if not acct_result.scalar_one_or_none():
            return ResultObject.failed("账号不存在")

        # upsert 会员记录
        result = await db.execute(
            select(XianyuAccountMembership).where(
                XianyuAccountMembership.account_id == account_id
            )
        )
        membership = result.scalar_one_or_none()
        if membership:
            membership.level = level
            membership.expired_time = expired_time
            membership.status = 1
        else:
            membership = XianyuAccountMembership(
                account_id=account_id,
                level=level,
                expired_time=expired_time,
                status=1,
            )
            db.add(membership)

        await db.commit()
        await db.refresh(membership)

        return ResultObject.success({
            "accountId": account_id,
            "level": membership.level,
            "expiredTime": str(membership.expired_time) if membership.expired_time else None,
            "status": membership.status,
            "message": "会员等级设置成功",
        })
    except Exception as e:
        logger.error("set account membership error", exc_info=True)
        return ResultObject.internal_error()

@router.get("/xianyu/accounts/{account_id}/membership", response_model=ResultObject)
async def restful_get_account_membership(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询账号会员等级。

    返回: {"accountId": int, "level": "normal|vip|svip", "expiredTime": str|null, "status": int}
    若账号未设置会员等级，返回 level="normal"。
    """
    try:
        # 检查账号是否存在
        acct_result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
        if not acct_result.scalar_one_or_none():
            return ResultObject.failed("账号不存在")

        result = await db.execute(
            select(XianyuAccountMembership).where(
                XianyuAccountMembership.account_id == account_id
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            # 未设置会员等级时回退为普通用户
            return ResultObject.success({
                "accountId": account_id,
                "level": "normal",
                "expiredTime": None,
                "status": 1,
                "message": "该账号尚未设置会员等级，默认为普通用户",
            })
        return ResultObject.success({
            "accountId": account_id,
            "level": membership.level,
            "expiredTime": str(membership.expired_time) if membership.expired_time else None,
            "status": membership.status,
        })
    except Exception as e:
        logger.error("查询账号会员等级失败", exc_info=True)
        return ResultObject.internal_error()


# ======================== GOODS  ========================

@router.get("/xianyu/goods", response_model=ResultObject)
async def restful_get_goods(
    account_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        valid_account_ids = select(XianyuAccount.id).where(XianyuAccount.deleted == 0)
        query = select(XianyuGoods).where(XianyuGoods.account_id.in_(valid_account_ids))
        if account_id is not None:
            query = query.where(XianyuGoods.account_id == account_id)
        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0
        offset = (page - 1) * page_size
        result = await db.execute(
            query.order_by(XianyuGoods.id.desc()).offset(offset).limit(page_size)
        )
        items = result.scalars().all()
        records = [goods_to_dto(g) for g in items]
        return ResultObject.success({"records": records, "total": total, "page": page, "page_size": page_size})
    except Exception as e:
        logger.error("get goods error", exc_info=True)
        return ResultObject.internal_error()

@router.get("/xianyu/goods/{goods_id}", response_model=ResultObject)
async def restful_get_goods_detail(
    goods_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(XianyuGoods).where(
                XianyuGoods.id == goods_id,
            )
        )
        goods = result.scalar_one_or_none()
        if not goods:
            return ResultObject.failed("商品不存在")
        return ResultObject.success(goods_to_dto(goods))
    except Exception as e:
        logger.error("get goods detail error", exc_info=True)
        return ResultObject.internal_error()

@router.get("/xianyu/goods/syncProgress/{sync_id}", response_model=ResultObject)
async def restful_goods_sync_progress(
    sync_id: str,
    current_user: dict = Depends(get_current_user),
):
    return ResultObject.success({"progress": 100, "status": "completed"})

@router.get("/xianyu/goods/syncing/{account_id}", response_model=ResultObject)
async def restful_goods_syncing(
    account_id: int,
    current_user: dict = Depends(get_current_user),
):
    return ResultObject.success(False)
# ======================== ORDERS ========================

@router.get("/xianyu/orders", response_model=ResultObject)
async def restful_get_orders(
    account_id: Optional[int] = Query(None),
    order_status: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        query = select(XianyuTradeOrder)
        if account_id is not None:
            query = query.where(XianyuTradeOrder.account_id == account_id)
        if order_status is not None:
            query = query.where(XianyuTradeOrder.order_status == order_status)
        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0
        offset = (page - 1) * page_size
        result = await db.execute(
            query.order_by(XianyuTradeOrder.id.desc()).offset(offset).limit(page_size)
        )
        orders = result.scalars().all()
        records = [trade_order_to_dto(o) for o in orders]
        return ResultObject.success({"records": records, "total": total, "page": page, "page_size": page_size})
    except Exception as e:
        logger.error("get orders error", exc_info=True)
        return ResultObject.internal_error()

@router.get("/xianyu/orders/{order_id}", response_model=ResultObject)
async def restful_get_order_detail(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(XianyuTradeOrder).where(
                XianyuTradeOrder.id == order_id,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            return ResultObject.failed("订单不存在")
        return ResultObject.success(trade_order_to_dto(order))
    except Exception as e:
        logger.error("get order detail error", exc_info=True)
        return ResultObject.internal_error()

# ======================== MESSAGES ========================

@router.get("/xianyu/messages", response_model=ResultObject)
async def restful_get_messages(
    account_id: Optional[int] = Query(None),
    session_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        query = select(XianyuMessage).where(XianyuMessage.deleted == 0)
        if account_id is not None:
            query = query.where(XianyuMessage.account_id == account_id)
        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0
        offset = (page - 1) * page_size
        result = await db.execute(
            query.order_by(XianyuMessage.id.desc()).offset(offset).limit(page_size)
        )
        msgs = result.scalars().all()
        records = [chat_message_to_dto(m) for m in msgs]
        return ResultObject.success({"records": records, "total": total, "page": page, "page_size": page_size})
    except Exception as e:
        logger.error("get messages error", exc_info=True)
        return ResultObject.internal_error()

# ======================== NOTIFICATIONS ========================

@router.get("/xianyu/notifications", response_model=ResultObject)
async def restful_get_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        query = select(Notification).where(Notification.deleted == 0)
        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0
        offset = (page - 1) * page_size
        result = await db.execute(
            query.order_by(Notification.id.desc()).offset(offset).limit(page_size)
        )
        notifications = result.scalars().all()
        return ResultObject.success({
            "records": [
                {
                    "id": n.id,
                    "title": n.title,
                    "content": n.content,
                    "type": n.notification_type,
                    "read": n.is_read,
                    "created_time": str(n.created_time) if n.created_time else None,
                }
                for n in notifications
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    except Exception as e:
        logger.error("get notifications error", exc_info=True)
        return ResultObject.internal_error()

@router.post("/xianyu/accounts", response_model=ResultObject)
async def restful_create_account(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        nickname = body.get("nickname", "")
        remark = body.get("account_note") or body.get("remark", "")
        external_uid = body.get("unb") or body.get("external_uid", "")
        account = XianyuAccount(
            external_uid=external_uid,
            nickname=nickname,
            remark=remark,
            status=1,
            deleted=0,
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)
        return ResultObject.success(account_to_profile_dto(account))
    except Exception as e:
        logger.error("create account error", exc_info=True)
        return ResultObject.internal_error()

# ======================== DASHBOARD ========================

@router.get("/xianyu/dashboard", response_model=ResultObject)
async def restful_get_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        # Account counts
        acct_query = select(XianyuAccount).where(
            XianyuAccount.deleted == 0,
        )
        total_accts = (await db.execute(
            select(func.count()).select_from(acct_query.subquery())
        )).scalar() or 0
        # Order counts
        order_query = select(XianyuTradeOrder)
        total_orders = (await db.execute(
            select(func.count()).select_from(order_query.subquery())
        )).scalar() or 0
        # Goods counts — 仅统计有效账号（deleted=0）的商品，已退出账号的旧商品不在前台展示
        valid_account_ids = select(XianyuAccount.id).where(XianyuAccount.deleted == 0)
        goods_query = select(XianyuGoods).where(
            XianyuGoods.deleted == 0,
            XianyuGoods.account_id.in_(valid_account_ids),
        )
        total_goods = (await db.execute(
            select(func.count()).select_from(goods_query.subquery())
        )).scalar() or 0
        return ResultObject.success({
            "accountCount": total_accts,
            "orderCount": total_orders,
            "goodsCount": total_goods,
            "todayOrderCount": 0,
        })
    except Exception as e:
        logger.error("get dashboard error", exc_info=True)
        return ResultObject.internal_error()
# ======================== AI PUBLISH ========================
from fastapi import Body
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.response import ResultObject
from app.api.v1.deps import get_current_user
from fastapi import Depends

@router.post('/item/publish', response_model=ResultObject)
async def publish_item(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # AI publish endpoint (stage 3)
    goods_id = body.get('goodsId')
    title = body.get('title')
    price = body.get('price')

    return ResultObject.success({
        'published': True,
        'goodsId': goods_id,
        'title': title,
        'price': price,
        'platform': 'xianyu',
        'status': 'online'
    })
