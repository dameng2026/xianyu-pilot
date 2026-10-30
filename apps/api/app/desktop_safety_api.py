"""Operator actions have a separate, ephemeral, same-origin desktop session."""
import secrets
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field
from app.core.response import ResultObject
from app.desktop_inventory_api import Id,local_write_boundary
from app.services.desktop_inventory import InventoryError

router=APIRouter(prefix="/api/desktop/safety")


@router.get("/session")
def operator_session(request:Request):
    if request.headers.get("x-desktop-ui")!="delivery-safety" or request.headers.get("sec-fetch-site")!="same-origin":
        raise InventoryError("请从本机发货安全工作台建立操作会话",403)
    runtime=request.app.state.platform_runtime
    return ResultObject.success({"token":request.app.state.safety_action_token,
        "actionsAvailable":bool(runtime and runtime.running),"expiresOnRestart":True})


def operator_authorized(request:Request):
    supplied=request.headers.get("x-desktop-action", "")
    if not supplied or not secrets.compare_digest(supplied,request.app.state.safety_action_token):
        raise InventoryError("本机操作会话无效，请刷新安全工作台",403)
    local_write_boundary(request)
    runtime=request.app.state.platform_runtime
    if not request.app.state.ready or runtime is None or not runtime.running:
        raise InventoryError("真实平台尚未连接，仅可查看本地保留状态",503)
    return runtime


class OperatorRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestId:str=Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")


async def perform(runtime,record_id,request_key,retry):
    try:
        return ResultObject.success(await runtime.reconcile_record(record_id,retry_confirmation=retry,request_key=request_key))
    except InventoryError:
        raise
    except Exception as exc:
        raise InventoryError("官方状态核验失败，原库存绑定保留；请稍后重新核验",503) from exc


@router.post("/records/{record_id}/reconcile")
async def reconcile(record_id:Id,body:OperatorRequest,runtime=Depends(operator_authorized)):
    return await perform(runtime,record_id,body.requestId,False)


@router.post("/records/{record_id}/retry-confirmation")
async def retry_confirmation(record_id:Id,body:OperatorRequest,runtime=Depends(operator_authorized)):
    return await perform(runtime,record_id,body.requestId,True)
