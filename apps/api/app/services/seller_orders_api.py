"""Authenticated H5 order adapter. Exact remote contract must be verified before enabling.

The requested seller.order.list and the repository's merchant.sold.get are
explicit alternatives, never silent fallbacks. Unknown statuses remain unknown.
"""
import hashlib
import json
import time
from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
import httpx
from app.services.official_delivery_contracts import OfficialOrder,WAITING


class PlatformOrderError(RuntimeError):
    pass


@dataclass
class SellerSession:
    account_id:int
    seller_id:str
    cookie:str=field(repr=False)


class SellerOrdersAPI:
    def __init__(self,sessions,*,contract_verified=False,api_name="mtop.taobao.idle.seller.order.list",transport=None,credential_updates=None):
        if api_name not in ("mtop.taobao.idle.seller.order.list","mtop.taobao.idle.trade.merchant.sold.get"):
            raise ValueError("Unsupported seller order endpoint")
        self.sessions,self.contract_verified,self.api_name,self.transport=sessions,contract_verified,api_name,transport
        self.credential_updates=credential_updates

    async def call(self,account_id,api,data):
        if api not in (self.api_name,"mtop.taobao.idle.logistic.consign.dummy"):
            raise PlatformOrderError("unsupported_platform_operation")
        if not self.contract_verified:
            raise PlatformOrderError("order_contract_unverified")
        session=self.sessions(account_id)
        cookies=dict(part.strip().split("=",1) for part in session.cookie.split(";") if "=" in part)
        if session.account_id!=account_id or cookies.get("unb")!=session.seller_id:
            raise PlatformOrderError("seller_session_mismatch")
        token=cookies.get("_m_h5_tk", "").split("_",1)[0]
        if not token:
            raise PlatformOrderError("session_expired")
        payload=json.dumps(data,separators=(",",":"),ensure_ascii=False)
        timestamp=str(int(time.time()*1000))
        sign=hashlib.md5(f"{token}&{timestamp}&34839810&{payload}".encode()).hexdigest()
        async with httpx.AsyncClient(transport=self.transport,trust_env=False,follow_redirects=False,timeout=12) as client:
            response=await client.post(f"https://h5api.m.goofish.com/h5/{api}/1.0/",params={
                "jsv":"2.7.2","appKey":"34839810","t":timestamp,"sign":sign,"v":"1.0","type":"originaljson",
                "dataType":"json","api":api,"sessionOption":"AutoLoginOnly"},
                data={"data":payload},headers={"Cookie":session.cookie,"Origin":"https://www.goofish.com","Referer":"https://seller.goofish.com/","Cache-Control":"no-cache"})
        if response.status_code!=200 or len(response.content)>2*1024*1024:
            raise PlatformOrderError("platform_response_invalid")
        updates=dict(response.cookies)
        if updates:
            if "unb" in updates and updates["unb"]!=session.seller_id:
                raise PlatformOrderError("refreshed_seller_mismatch")
            merged={**cookies,**updates}
            updated_cookie="; ".join(f"{key}={value}" for key,value in merged.items())
            if updated_cookie!=session.cookie:
                if self.credential_updates is not None:
                    await self.credential_updates(account_id,"cookie",updated_cookie)
                session.cookie=updated_cookie
        try:
            result=response.json()
            ret=result.get("ret")
            if not isinstance(ret,list) or not ret or not isinstance(ret[0],str) or not ret[0].startswith("SUCCESS::"):
                raise PlatformOrderError("platform_rejected_or_login_expired")
            if not isinstance(result.get("data"),dict):
                raise PlatformOrderError("platform_schema_changed")
            return result["data"]
        except (ValueError,AttributeError) as exc:
            raise PlatformOrderError("platform_schema_changed") from exc

    def parse_order(self,account_id,row):
        try:
            session=self.sessions(account_id)
            common=row.get("commonData",row)
            buyer=row.get("buyerInfoVO",{})
            price=row.get("priceVO",{})
            quantity=common.get("quantity",price.get("buyNum"))
            # No guessed quantity=1, truthiness conversion or Chinese status mapping.
            if isinstance(quantity,str) and quantity.isascii() and quantity.isdigit():
                quantity=int(quantity)
            if "amountCent" in common:
                cents=common["amountCent"]
            else:
                amount=Decimal(str(price["totalPrice"]))*100
                if not amount.is_finite() or amount!=amount.to_integral_value():
                    raise ValueError("invalid amount")
                cents=int(amount)
            refund=common.get("inRefund")
            if not isinstance(refund,bool):
                raise ValueError("refund flag required")
            return OfficialOrder(account_id=account_id,seller_id=common.get("sellerId",session.seller_id),
                order_id=common["orderId"],buyer_id=common.get("buyerId",buyer.get("buyerId")),item_id=common["itemId"],
                sku_id=common["skuId"],quantity=quantity,amount_cent=cents,status=common["orderStatus"],refunding=refund)
        except (KeyError,TypeError,ValueError,InvalidOperation,AttributeError) as exc:
            raise PlatformOrderError("platform_order_schema_changed") from exc

    async def list_waiting(self,account_id,page=1):
        if not 1<=page<=100:
            raise PlatformOrderError("pagination_limit")
        data=await self.call(account_id,self.api_name,{"pageNumber":page,"pageSize":50,"orderStatus":WAITING})
        module=data.get("module",data)
        rows=module.get("items",module.get("orders"))
        more=module.get("hasNext",module.get("hasMore"))
        if not isinstance(rows,list) or not isinstance(more,bool):
            raise PlatformOrderError("pagination_schema_changed")
        orders=[self.parse_order(account_id,row) for row in rows]
        if any(order.account_id!=account_id or order.seller_id!=self.sessions(account_id).seller_id for order in orders):
            raise PlatformOrderError("seller_mismatch")
        return orders,more

    async def get_order(self,account_id,order_id):
        data=await self.call(account_id,self.api_name,{"pageNumber":1,"pageSize":50,"orderIds":order_id})
        module=data.get("module",data)
        rows=module.get("items",module.get("orders"))
        if not isinstance(rows,list):
            raise PlatformOrderError("order_lookup_schema_changed")
        matches=[self.parse_order(account_id,row) for row in rows]
        matches=[order for order in matches if order.order_id==order_id]
        if len(matches)!=1:
            raise PlatformOrderError("order_not_uniquely_verified")
        order=matches[0]
        if order.seller_id!=self.sessions(account_id).seller_id:
            raise PlatformOrderError("seller_mismatch")
        return order
