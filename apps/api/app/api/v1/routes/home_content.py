import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....core.response import ResultObject
from ....services.local_home_content import load_local_home_content
# 读取当前安装的本地公告内容。

logger = logging.getLogger(__name__)

router = APIRouter(tags=["home-content"])


@router.get("/announcement/list", response_model=ResultObject[list])
async def get_announcement_list(
    db: AsyncSession = Depends(get_db),
):
    del db
    try:
        local_content = await asyncio.to_thread(load_local_home_content)
        return ResultObject.success(local_content.get("announcements", []))
    except RuntimeError:
        logger.error("Local announcement content storage is unavailable")
        return ResultObject.failed("公告内容服务暂不可用，请联系管理员检查内容存储", code=503)
