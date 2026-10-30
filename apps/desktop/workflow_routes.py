import asyncio

from fastapi import APIRouter, HTTPException

from .qq_connector import verify_account
from .workflow_models import AccountInput, ProductInput, ServiceInput, OrderInput, TaskAction


def workflow_router(repository, worker, mcp):
    router = APIRouter(prefix="/api/workflow")

    @router.get("/state")
    def state():
        return {**repository.resources(), "worker_healthy": not worker.failure,
                "real_xianyu_connected": False, "mcp_url": f"http://127.0.0.1:{mcp.port}/mcp"}

    from .mcp_server import GrantInput

    @router.get("/mcp/grants")
    def grants(): return {"grants": mcp.grants()}

    @router.post("/mcp/grants")
    def create_grant(body: GrantInput): return mcp.create_grant(body)

    @router.post("/mcp/grants/{identity}/revoke")
    def revoke_grant(identity: str):
        mcp.revoke(identity)
        return {"revoked": True}

    @router.post("/accounts")
    def account(body: AccountInput):
        return repository.save_account(body)

    @router.post("/accounts/{identity}/save")
    def edit_account(identity: str, body: AccountInput):
        return repository.save_account(body, identity)

    @router.post("/accounts/{identity}/verify")
    async def verify(identity: str):
        return await asyncio.to_thread(verify_account, repository, identity)

    @router.post("/accounts/{identity}/unbind")
    def unbind(identity: str):
        repository.unbind(identity)
        return {"unbound": True, "upstream_grant_revoked": False}

    @router.post("/services")
    def service(body: ServiceInput):
        return repository.save_service(body)

    @router.post("/services/{identity}/save")
    def edit_service(identity: str, body: ServiceInput):
        return repository.save_service(body, identity)

    @router.post("/products")
    def product(body: ProductInput):
        return repository.save_product(body)

    @router.post("/products/{identity}/save")
    def edit_product(identity: str, body: ProductInput):
        return repository.save_product(body, identity)

    @router.post("/tasks", status_code=201)
    def task(body: OrderInput):
        if worker.failure:
            raise HTTPException(503, "工作流处理器已停止，请先重启服务")
        row, created = repository.create_order(body)
        return {"task": repository.summary(row), "created": created}

    @router.get("/tasks/{identity}")
    def detail(identity: str):
        return repository.detail(identity)

    @router.post("/tasks/{identity}/actions")
    def action(identity: str, body: TaskAction):
        return repository.action(identity, body)

    return router
