"""Bridge existing IM ACK semantics to per-segment receipts, with no MySQL path."""
import asyncio
from app.services.desktop_delivery_contracts import StepResult
from app.services.seller_orders_api import PlatformOrderError
from app.services.delivery_resume import ResumeDetector


class OfficialDeliverySender:
    def __init__(self,source,clients,resolve_session,upload_image=None):
        self.source,self.clients,self.resolve_session=source,clients,resolve_session
        self.upload_image=upload_image

    def is_ready(self,account_id):
        return bool(self.clients.get(account_id) and self.clients[account_id].is_connected)

    async def send(self,work,order,image_bytes=None):
        client=self.clients.get(order.account_id)
        if not client or client.account_id!=order.account_id or client.unb!=order.seller_id:
            return StepResult("not_sent","local:im_seller_mismatch")
        if not self.is_ready(order.account_id):
            return StepResult("not_sent","local:im_disconnected",True)
        try:
            # The resolver must query/verify the IM conversation peer, never
            # guess a CID from buyer content or from an unrelated order.
            cid=await self.resolve_session(order.account_id,order.buyer_id,order.item_id)
            if not isinstance(cid,str) or not cid:
                return StepResult("not_sent","local:conversation_unverified",True)
            image=None
            if work.kind=="image":
                if self.upload_image is None:
                    return StepResult("not_sent","local:image_upload_unconfigured")
                image=await self.upload_image(order.account_id,image_bytes)
                if not isinstance(image,tuple) or len(image)!=3 or not str(image[0]).startswith("https://"):
                    return StepResult("not_sent","local:image_upload_invalid",True)
        except Exception:
            return StepResult("not_sent","local:pre_send_preparation_failed",True)
        client=self.clients[order.account_id]
        if work.kind=="image":
            response=await client.send_image_message(cid,order.buyer_id,image[0],width=image[1],height=image[2],persist=False)
        else:
            response=await client.send_text_message(cid,order.buyer_id,work.payload["content"],persist=False)
        if isinstance(response,dict) and response.get("code")==200 and response.get("uuid") and not response.get("deliveryUnknown"):
            return StepResult("confirmed","im:"+str(response["uuid"]))
        return StepResult("unknown")

    async def confirm_shipment(self,work,order):
        if hasattr(self.source,"sessions") and self.source.sessions(order.account_id).seller_id!=order.seller_id:
            return StepResult("not_sent","local:confirmation_seller_mismatch")
        # No automatic request retry on an ambiguous platform confirmation.
        await self.source.call(order.account_id,"mtop.taobao.idle.logistic.consign.dummy",
            {"orderId":order.order_id,"tradeText":"","picList":[],"newUnconsign":True})
        return StepResult("confirmed","official:consign-ack:"+work.operation_id)


class OfficialDeliveryRuntime:
    """Explicit lifecycle composition; ordinary desktop startup stays offline.

    Clients must be constructed using the SQLite credential callbacks below or
    an equivalent authenticated local session. No public HTTP enable switch.
    """
    def __init__(self,monitor,clients,seller_ids):
        self.monitor,self.clients,self.seller_ids=monitor,clients,seller_ids
        self.stop_event=asyncio.Event()
        self.tasks=[]
        self.running=False
        self.resume_detector=ResumeDetector()

    def pause(self,reason="manual"):
        self.monitor.gate.block(reason)

    async def resume(self,reason="manual",*,expected_revision=None):
        if reason=="shutdown":
            raise ValueError("shutdown cannot be resumed")
        revision=self.monitor.gate.revision(reason) if expected_revision is None else expected_revision
        if self.monitor.gate.revision(reason)!=revision:
            return False
        self.notify_resume(reason+"_recovery")
        return self.monitor.gate.clear(reason,expected_revision=revision)

    async def desktop_status(self):
        reasons=self.monitor.gate.reasons
        if not self.running:
            state="offline"
        elif "shutdown" in reasons:
            state="stopping"
        elif reasons:
            state="paused"
        elif self.monitor._resume_required:
            state="reconciling"
        elif not self.clients or not all(self.monitor.sender.is_ready(aid) for aid in self.clients):
            state="paused"
        else:
            with self.monitor.store.connection() as conn:
                rows=conn.execute("SELECT state FROM delivery_monitor_state WHERE account_id IN (%s)" % ','.join('?' for _ in self.clients),tuple(self.clients)).fetchall()
            state="running" if len(rows)==len(self.clients) and {r[0] for r in rows}=={"monitoring"} else "attention"
        return {"state":state,"pauseReasons":list(reasons),"accountCount":len(self.clients)}

    def notify_resume(self,reason="system_resumed"):
        for aid in self.clients:
            self.monitor.request_reconciliation(aid,reason)

    async def reconcile_record(self,*args,**kwargs):
        return await self.monitor.reconcile_record(*args,**kwargs)

    async def watch_continuity(self):
        previous={aid:self.monitor.sender.is_ready(aid) for aid in self.clients}
        self.resume_detector.sample()
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(),timeout=5)
            except asyncio.TimeoutError:
                if self.resume_detector.sample():
                    self.notify_resume()
                for aid in self.clients:
                    connected=self.monitor.sender.is_ready(aid)
                    if connected!=previous[aid]:
                        self.monitor.request_reconciliation(aid,"im_reconnected" if connected else "im_disconnected")
                    previous[aid]=connected

    async def start(self):
        if not getattr(self.monitor.source,"contract_verified",False):
            raise PlatformOrderError("order_contract_unverified")
        self.monitor._stopping=False
        self.stop_event.clear()
        try:
            for aid,client in self.clients.items():
                if client.account_id!=aid or client.unb!=self.seller_ids[aid]:
                    raise ValueError("IM seller mismatch")
                async def callback(account_id,message,owner=aid):
                    if account_id!=owner:
                        raise ValueError("IM account mismatch")
                    await self.monitor.on_ws_message(account_id,message,self.seller_ids[owner])
                client.on_message_callback=callback
                await client.start()
                self.monitor.request_reconciliation(aid,"startup")
                self.tasks.append(asyncio.create_task(self.monitor.run(aid,self.stop_event)))
            self.tasks.append(asyncio.create_task(self.watch_continuity()))
            self.running=True
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        self.pause("shutdown")
        self.monitor._stopping=True
        await asyncio.gather(*(client.stop() for client in self.clients.values()),return_exceptions=True)
        # Wait for account executors while they finish their short DB commits
        # and bounded external calls. No cancellation of a to_thread commit.
        for aid in self.clients:
            async with self.monitor.lock(aid):
                await asyncio.to_thread(self.monitor.store.recover_interrupted,aid)
        self.stop_event.set()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tasks=[]
        self.running=False


def create_desktop_im_client(session,credential_updates):
    """Keep the original heartbeat/reconnect protocol, redirect credential writes.

    credential_updates is an async callback owned by the local credential vault;
    it MUST persist only encrypted values below data/profiles or app.sqlite3.
    Captcha failure pauses for user action instead of starting legacy crawlers.
    """
    from app.services.ws_client import XianyuWebSocketClient
    from app.services.ws_token import get_ws_token_with_refreshed_m_h5_tk,invalidate_cached_token
    class DesktopIMClient(XianyuWebSocketClient):
        async def _update_token_in_db(self,value):
            await credential_updates(session.account_id,"token",value)
        async def _update_cookie_in_db(self,value):
            session.cookie=value
            await credential_updates(session.account_id,"cookie",value)
        async def _update_cookie_status(self,status,code=None,message=None):
            await credential_updates(session.account_id,"status",str(status))
        async def _auto_solve_captcha_after_failure(self,scene):
            await self._update_cookie_status(0)
        async def _refresh_token(self):
            token,m_h5_tk,error,cookie=await asyncio.to_thread(get_ws_token_with_refreshed_m_h5_tk,self.cookie_str,self.m_h5_tk)
            if not token:
                invalidate_cached_token(self.unb)
                await self._update_cookie_status(0,error)
                return False
            self._access_token=token
            if m_h5_tk and m_h5_tk!=self.m_h5_tk:
                self.m_h5_tk=m_h5_tk
                await self._update_token_in_db(m_h5_tk)
            if cookie and cookie!=self.cookie_str:
                self.cookie_str=cookie
                await self._update_cookie_in_db(cookie)
            return True
    cookies=dict(part.strip().split("=",1) for part in session.cookie.split(";") if "=" in part)
    if cookies.get("unb")!=session.seller_id:
        raise ValueError("IM seller session mismatch")
    return DesktopIMClient(session.account_id,session.cookie,cookies.get("_m_h5_tk", ""),session.seller_id,notifications_enabled=False)


def compose_official_runtime(path,account_ids,sessions,credential_updates,resolve_session,*,contract_verified=False,
                             api_name="mtop.taobao.idle.seller.order.list",interval=20):
    """Host integration seam after credential + platform-contract verification.

    This does not enable itself from environment variables or an HTTP payload.
    The caller supplies the local encrypted vault and authenticated IM resolver.
    """
    if not contract_verified:
        raise PlatformOrderError("order_contract_unverified")
    from app.services.seller_orders_api import SellerOrdersAPI
    from app.services.official_delivery_monitor import OfficialDeliveryMonitor
    source=SellerOrdersAPI(sessions,contract_verified=True,api_name=api_name,credential_updates=credential_updates)
    clients={aid:create_desktop_im_client(sessions(aid),credential_updates) for aid in account_ids}
    async def upload(account_id,image_bytes):
        from app.services.auto_category import upload_image_to_xianyu
        return await asyncio.to_thread(upload_image_to_xianyu,sessions(account_id).cookie,image_bytes)
    sender=OfficialDeliverySender(source,clients,resolve_session,upload)
    monitor=OfficialDeliveryMonitor(path,source,sender,interval=interval)
    return OfficialDeliveryRuntime(monitor,clients,{aid:sessions(aid).seller_id for aid in account_ids})
