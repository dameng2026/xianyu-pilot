import logging
from datetime import datetime
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from ....core.database import get_db
from ....core.response import ResultObject
from ....core.cookie_crypto import encrypt_cookie_for_storage
from ....core.unavailable_features import (
    ACCOUNT_LOGIN_CREDENTIAL_UNAVAILABLE,
    feature_unavailable,
)
from ....models.entities import (
    XianyuAccount,
    XianyuAccountAuth,
    XianyuAccountRuntime,
    XianyuAccountMembership,
)
from ....schemas.account import (
    AccountReqDTO, ManualAddAccountReqDTO, UpdateAccountReqDTO,
    DeleteAccountReqDTO, GetAccountDetailReqDTO, RefreshAccountProfileReqDTO,
    AccountProfileDTO, GetAccountListRespDTO, AddAccountRespDTO,
    UpdateAccountRespDTO, DeleteAccountRespDTO, GetAccountDetailRespDTO,
    RefreshAccountProfileRespDTO
)
from ..deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/account")


def account_to_dto(account: XianyuAccount, membership=None) -> AccountProfileDTO:
    """将新实体 XianyuAccount 转换为 AccountProfileDTO

    membership: 可选的 XianyuAccountMembership 实体，用于填充会员等级字段。
    """
    ip_location = None
    if account.province or account.city:
        ip_location = f"{account.province or ''} {account.city or ''}".strip()

    dto = AccountProfileDTO(
        id=account.id,
        external_uid=account.external_uid,
        nickname=account.nickname,
        avatar_url=account.avatar_url,
        remark=account.remark,
        province=account.province,
        city=account.city,
        ip_location=ip_location,
        account_level=account.account_level,
        introduction=account.introduction,
        followers=account.followers,
        following=account.following,
        sold_count=account.sold_count,
        review_num=account.review_num,
        seller_level=account.seller_level,
        praise_ratio=account.praise_ratio,
        fish_shop_score=float(account.fish_shop_score) if account.fish_shop_score is not None else None,
        fish_shop_user=account.fish_shop_user,
        status=account.status,
        created_time=str(account.created_time) if account.created_time else None,
        # 向后兼容字段
        unb=account.external_uid,
        account_note=account.remark,
        display_name=account.nickname,
        avatar=account.avatar_url,
        proxy_password="***",
    )
    if membership is not None:
        dto.membership_level = membership.level
        dto.membership_expired_time = str(membership.expired_time) if membership.expired_time else None
        dto.membership_status = membership.status
    return dto


@router.post("/list", response_model=ResultObject[GetAccountListRespDTO])
async def get_account_list(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        query = select(XianyuAccount, XianyuAccountMembership).outerjoin(
            XianyuAccountMembership,
            XianyuAccountMembership.account_id == XianyuAccount.id,
        )
        result = await db.execute(query)
        rows = result.all()
        account_list = [account_to_dto(a, m) for a, m in rows]
        return ResultObject.success(GetAccountListRespDTO(accounts=account_list))
    except Exception as e:
        logger.error("获取账号列表失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/add", response_model=ResultObject[AddAccountRespDTO])
async def add_account(
    req: AccountReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        if not req.cookie:
            return ResultObject.failed("Cookie不能为空")

        unb = extract_unb_from_cookie(req.cookie)
        if not unb:
            return ResultObject.failed("无法从Cookie中提取UNB信息")
        existing = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.external_uid == unb,
            )
        )
        if existing.scalar_one_or_none():
            return ResultObject.failed("账号已存在")

        account = XianyuAccount(
            external_uid=unb,
            remark=req.account_note,
            status=1
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)

        auth = XianyuAccountAuth(
            account_id=account.id,
            encrypted_cookie=encrypt_cookie_for_storage(req.cookie),
            encrypted_token=encrypt_cookie_for_storage(extract_m_h5_tk_from_cookie(req.cookie)) if extract_m_h5_tk_from_cookie(req.cookie) else None,
            cookie_status=0,
            last_login_status_code="COOKIE_UPDATED",
            last_login_status_message="Cookie 已更新，等待统一登录校验",
        )
        db.add(auth)
        db.add(XianyuAccountRuntime(
            account_id=account.id,
            cookie_status=0,
            last_login_status_code="COOKIE_UPDATED",
            last_login_status_message="Cookie 已更新，等待统一登录校验",
        ))
        await db.commit()

        return ResultObject.success(AddAccountRespDTO(
            account_id=account.id,
            message="添加成功"
        ))
    except Exception as e:
        logger.error("添加账号失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/manualAdd", response_model=ResultObject[AddAccountRespDTO])
async def manual_add_account(
    req: ManualAddAccountReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        if not req.cookie:
            return ResultObject.failed("Cookie不能为空")

        unb = extract_unb_from_cookie(req.cookie)
        if not unb:
            return ResultObject.failed("无法从Cookie中提取UNB信息")
        existing = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.external_uid == unb,
            )
        )
        if existing.scalar_one_or_none():
            return ResultObject.failed("账号已存在")

        account = XianyuAccount(
            external_uid=unb,
            remark=req.account_note,
            status=1
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)

        auth = XianyuAccountAuth(
            account_id=account.id,
            encrypted_cookie=encrypt_cookie_for_storage(req.cookie),
            encrypted_token=encrypt_cookie_for_storage(extract_m_h5_tk_from_cookie(req.cookie)) if extract_m_h5_tk_from_cookie(req.cookie) else None,
            cookie_status=0,
            last_login_status_code="COOKIE_UPDATED",
            last_login_status_message="Cookie 已更新，等待统一登录校验",
        )
        db.add(auth)
        db.add(XianyuAccountRuntime(
            account_id=account.id,
            cookie_status=0,
            last_login_status_code="COOKIE_UPDATED",
            last_login_status_message="Cookie 已更新，等待统一登录校验",
        ))
        await db.commit()

        return ResultObject.success(AddAccountRespDTO(
            account_id=account.id,
            message="添加成功"
        ))
    except Exception as e:
        logger.error("手动添加账号失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/update", response_model=ResultObject[UpdateAccountRespDTO])
async def update_account(
    req: UpdateAccountReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == req.account_id,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")

        if req.account_note is not None:
            account.remark = req.account_note.strip()

        await db.commit()
        return ResultObject.success(UpdateAccountRespDTO(message="更新成功"))
    except Exception as e:
        logger.error("更新账号失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/delete", response_model=ResultObject[DeleteAccountRespDTO])
async def delete_account(
    req: DeleteAccountReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == req.account_id,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")

        # 删除关联的认证信息
        await db.execute(
            XianyuAccountAuth.__table__.delete().where(
                XianyuAccountAuth.account_id == req.account_id
            )
        )
        await db.delete(account)
        await db.commit()
        return ResultObject.success(DeleteAccountRespDTO(message="删除成功"))
    except Exception as e:
        logger.error("删除账号失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/detail", response_model=ResultObject[GetAccountDetailRespDTO])
async def get_account_detail(
    req: GetAccountDetailReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        result = await db.execute(
            select(XianyuAccount, XianyuAccountMembership)
            .outerjoin(
                XianyuAccountMembership,
                XianyuAccountMembership.account_id == XianyuAccount.id,
            )
            .where(XianyuAccount.id == req.account_id)
        )
        row = result.first()
        if not row:
            return ResultObject.failed("账号不存在")
        account, membership = row
        return ResultObject.success(GetAccountDetailRespDTO(account=account_to_dto(account, membership)))
    except Exception as e:
        logger.error("获取账号详情失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/refreshProfile", response_model=ResultObject[RefreshAccountProfileRespDTO])
async def refresh_account_profile(
    req: RefreshAccountProfileReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id == req.account_id,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            return ResultObject.failed("账号不存在")
        return ResultObject.success(RefreshAccountProfileRespDTO(
            account=account_to_dto(account),
            message="刷新成功"
        ))
    except Exception as e:
        logger.error("刷新账号资料失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/loginCredential")
async def get_login_credential(
    current_user: dict = Depends(get_current_user)
):
    """Retired: no password-login consumer exists in this build."""

    feature_unavailable(ACCOUNT_LOGIN_CREDENTIAL_UNAVAILABLE)


def extract_unb_from_cookie(cookie: str) -> str:
    if not cookie:
        return None
    for part in cookie.split("; "):
        if part.startswith("unb="):
            return part[4:]
    return None


def extract_m_h5_tk_from_cookie(cookie: str) -> str:
    """从 Cookie 字符串中提取 _m_h5_tk 值。"""
    if not cookie:
        return ""
    import re
    match = re.search(r'_m_h5_tk=([^;]+)', cookie)
    return match.group(1) if match else ""


@router.post("/updateCookie", response_model=ResultObject[dict])
async def update_account_cookie(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """更新账号 Cookie，并在统一登录校验完成前保持不可用状态。
    
    请求体: {"accountId": 1, "cookie": "完整Cookie字符串"}
    """
    try:
        account_id = data.get("accountId")
        cookie = data.get("cookie", "")
        if not account_id or not cookie:
            return ResultObject.validate_failed("accountId 和 cookie 不能为空")
        # 提取 _m_h5_tk
        m_h5_tk = extract_m_h5_tk_from_cookie(cookie)
        if not m_h5_tk:
            logger.warning("updateCookie: Cookie 中未提取到 _m_h5_tk accountId=%s", account_id)

        # 更新 auth 表
        await db.execute(
            text(
                "UPDATE xianyu_account_auth SET encrypted_cookie = :cookie, "
                "encrypted_token = :token, cookie_status = 0, "
                "last_login_status_code = :code, last_login_status_message = :message, "
                "last_login_check_time = NOW(), updated_time = NOW() "
                "WHERE account_id = :aid"
            ),
            {
                "cookie": encrypt_cookie_for_storage(cookie),
                "token": encrypt_cookie_for_storage(m_h5_tk) if m_h5_tk else None,
                "code": "COOKIE_UPDATED",
                "message": "Cookie 已更新，等待统一登录校验",
                "aid": account_id,
            }
        )

        # 更新 runtime 表
        await db.execute(
            text(
                "UPDATE xianyu_account_runtime SET cookie_status = 0, "
                "last_login_status_code = :code, last_login_status_message = :message, "
                "last_login_check_time = NOW(), updated_time = NOW() "
                "WHERE account_id = :aid"
            ),
            {
                "code": "COOKIE_UPDATED",
                "message": "Cookie 已更新，等待统一登录校验",
                "aid": account_id,
            }
        )

        await db.commit()

        # 此处仅持久化新凭据，不能把“已保存”等同于“已验证”。后续统一
        # 登录校验确认可用后才会恢复 cookie_status 并清除失效通知状态。
        logger.info(
            "updateCookie: 已更新账号 Cookie accountId=%s credential_present=%s",
            account_id, ("present" if m_h5_tk else "missing")
        )

        return ResultObject.success({
            "message": "Cookie 更新成功",
            "accountId": account_id,
            "hasToken": bool(m_h5_tk),
        })
    except Exception as exc:
        logger.error(
            "updateCookie failed errorType=%s",
            type(exc).__name__,
        )
        return ResultObject.internal_error()


# ============================================================
# Cookie/Token 自动刷新调度器接口
# ============================================================
@router.get("/refresh/status", response_model=ResultObject[dict])
async def get_refresh_status(current_user: dict = Depends(get_current_user)):
    """获取 Cookie/Token 刷新调度器状态"""
    try:
        from app.services.cookie_token_refresher import get_dispatcher_status
        status = await get_dispatcher_status()
        return ResultObject.success(status)
    except Exception as exc:
        logger.error("获取刷新调度器状态失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/refresh/force", response_model=ResultObject[dict])
async def force_refresh_account(
    data: dict = {},
    current_user: dict = Depends(get_current_user)
):
    """手动触发单账号刷新
    请求体: {"accountId": 1, "refreshType": "all|cookie|mh5tk|ws_token"}
    """
    try:
        account_id = data.get("accountId")
        refresh_type = data.get("refreshType") or "all"
        if not account_id:
            return ResultObject.validate_failed("accountId 不能为空")
        if refresh_type not in ("all", "cookie", "mh5tk", "ws_token"):
            return ResultObject.validate_failed("refreshType 必须为 all/cookie/mh5tk/ws_token")

        from app.services.cookie_token_refresher import force_refresh_account as _force
        result = await _force(int(account_id), refresh_type)
        if result.get("success") is not True:
            error_code = str(result.get("errorCode") or "REFRESH_FAILED")
            if error_code == "ACCOUNT_NOT_FOUND":
                raise HTTPException(status_code=404, detail="账号不存在。")
            if error_code == "CREDENTIAL_MISSING":
                raise HTTPException(status_code=422, detail="账号凭据不完整，无法执行刷新。")
            raise HTTPException(
                status_code=503,
                detail="凭据刷新未完成，请检查账号状态后重试。",
            )
        return ResultObject.success(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Manual credential refresh failed errorType=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="凭据刷新服务暂不可用，请稍后重试。",
        ) from exc


@router.post("/refresh/start", response_model=ResultObject[dict])
async def start_refresh_scheduler(current_user: dict = Depends(get_current_user)):
    """启动刷新调度器（管理员操作）"""
    try:
        from app.services.cookie_token_refresher import start_dispatcher
        await start_dispatcher()
        return ResultObject.success({"message": "刷新调度器已启动"})
    except Exception as exc:
        logger.error("启动刷新调度器失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/refresh/stop", response_model=ResultObject[dict])
async def stop_refresh_scheduler(current_user: dict = Depends(get_current_user)):
    """停止刷新调度器（管理员操作）"""
    try:
        from app.services.cookie_token_refresher import stop_dispatcher
        await stop_dispatcher()
        return ResultObject.success({"message": "刷新调度器已停止"})
    except Exception as exc:
        logger.error("停止刷新调度器失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


# ============================================================
# 账号会员等级（同步商业版 xianyu_account_membership）
# ============================================================
@router.put("/{account_id}/membership", response_model=ResultObject[dict])
async def set_account_membership(
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
            select(XianyuAccount).where(XianyuAccount.id == account_id)
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
        logger.error("设置账号会员等级失败", exc_info=True)
        return ResultObject.internal_error()
