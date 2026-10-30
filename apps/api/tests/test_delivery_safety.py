"""Fault injection for permanent ownership, frozen content and B-only repair."""
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient

from test_desktop_delivery import store,order,plan,rows,Gateway,no_network
from test_official_delivery import configured,fact,variant
from app.services.desktop_delivery import DesktopDeliveryCoordinator
from app.services.desktop_delivery_contracts import StepResult
from app.services.desktop_inventory import InventoryError
from app.services.delivery_resume import ResumeDetector
from app.services.official_delivery_contracts import GoodsDeliveryConfig
from app.desktop_app import create_desktop_app
from app.core.config import Settings


@pytest.mark.parametrize('sql',[
    'UPDATE card_item SET status=0 WHERE id=1',
    'UPDATE card_item SET claim_token=NULL WHERE id=1',
    'UPDATE card_item SET used_order_id=NULL WHERE id=1',
    'UPDATE card_item SET deleted=1 WHERE id=1',
    "UPDATE card_item SET card_key='replacement' WHERE id=1",
    'DELETE FROM card_item WHERE id=1',
    'DELETE FROM delivery_card_reservation WHERE card_id=1',
    'UPDATE delivery_card_reservation SET order_id=999 WHERE card_id=1',
    "UPDATE delivery_record SET snapshot_json='{}' WHERE id=1",
    "UPDATE delivery_record SET receipt_mode='platform' WHERE id=1",
    "UPDATE delivery_record SET platform_item_id='other' WHERE id=1",
    'DELETE FROM delivery_record WHERE id=1',
    "UPDATE delivery_segment SET payload_json='{}' WHERE delivery_id=1",
    'DELETE FROM delivery_segment WHERE delivery_id=1',
    "INSERT INTO delivery_segment(delivery_id,position,kind,payload_json,operation_id,state,updated_time) VALUES(1,999,'text','{}','extra','pending','now')",
])
def test_database_rejects_releasing_rebinding_or_rewriting_delivery(store,sql):
    store.prepare(order(store),plan())
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection(write=True) as conn:
            conn.execute(sql)
    card=rows(store,'SELECT * FROM card_item WHERE id=1')[0]
    assert card['status']==1 and card['claim_token'] and card['used_order_id']==1


def test_reservation_is_occupied_not_counted_as_delivered_and_races_do_not_overlap(store):
    ids=[order(store,f'parallel-{i}',quantity=3) for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        delivery_ids=list(pool.map(lambda oid:store.prepare(oid,plan(image=False)),ids))
    reservations=rows(store,'SELECT * FROM delivery_card_reservation')
    assert len(reservations)==12 and len({r['card_id'] for r in reservations})==12
    stats=store.get_group(1,stats=True)
    assert stats['remainCount']==0 and stats['usedCount']==0
    assert len(set(delivery_ids))==4


def test_unknown_segment_cannot_be_reset_and_card_never_reenters_stock(store):
    did=store.prepare(order(store),plan())
    work=store.claim_next(did)
    store.finish(work,StepResult('unknown'))
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection(write=True) as conn:
            conn.execute("UPDATE delivery_segment SET state='pending' WHERE id=?",(work.segment_id,))
    assert store.detail(did)['safetyStatus']=='NEED_MANUAL_CHECK'
    assert store.get_group(1,stats=True)['remainCount']==11
    assert store.recover_interrupted()==0
    assert store.claim_next(did) is None


def test_frozen_image_survives_original_edits_and_new_orders_use_new_version(store):
    did=store.prepare(order(store),plan(instructions='version one'))
    frozen=store.detail(did)['assetReferences'][0]
    before=(store.path.parent/frozen).read_bytes()
    (store.path.parent/'assets/guide.png').write_bytes(b'version two')
    other=store.prepare(order(store,'second'),plan(instructions='version two'))
    assert store.detail(other)['assetReferences'][0]!=frozen
    gateway=Gateway()
    assert DesktopDeliveryCoordinator(store,gateway).execute(did)['workflowState']=='completed'
    assert (store.path.parent/frozen).read_bytes()==before
    assert any(w.payload['content']=='version one' for w in gateway.calls)
    assert not any(w.payload['content']=='version two' for w in gateway.calls)


@pytest.mark.asyncio
async def test_explicit_B_only_retry_after_timeout_never_sends_or_allocates_again(configured):
    store,rules,source,sender,monitor=configured
    v=variant(image=False)
    v=v.model_copy(update={'plan':v.plan.model_copy(update={'max_attempts':1})})
    rules.save_goods(1,GoodsDeliveryConfig(variants=(v,)))
    sender.confirm_results=[StepResult('unknown')]
    await monitor.poll_once(1)
    before=store.detail(1)
    assert before['contentState']=='DELIVERED' and before['safetyStatus']=='NEED_MANUAL_CHECK'
    sent=len(sender.sent)
    reservations=rows(store,'SELECT * FROM delivery_card_reservation')
    sender.ready=False  # B does not depend on a connected IM socket.
    repaired=await monitor.reconcile_record(1,retry_confirmation=True,request_key='operator-request-1')
    assert repaired['workflowState']=='completed'
    assert len(sender.sent)==sent and len(sender.confirmed)==2
    assert sender.confirmed[0].operation_id==sender.confirmed[1].operation_id
    assert rows(store,'SELECT * FROM delivery_card_reservation')==reservations
    await monitor.reconcile_record(1,retry_confirmation=True,request_key='operator-request-1')
    assert len(sender.confirmed)==2 and len(sender.sent)==sent


@pytest.mark.asyncio
async def test_replayed_operator_request_cannot_repeat_ambiguous_B(configured):
    store,rules,source,sender,monitor=configured
    sender.confirm_results=[StepResult('unknown'),StepResult('unknown')]
    await monitor.poll_once(1)
    await monitor.reconcile_record(1,retry_confirmation=True,request_key='same-request')
    await monitor.reconcile_record(1,retry_confirmation=True,request_key='same-request')
    assert len(sender.confirmed)==2
    assert store.detail(1)['safetyStatus']=='NEED_MANUAL_CHECK'


@pytest.mark.asyncio
async def test_official_shipped_proves_B_but_not_unknown_card_message(configured):
    store,rules,source,sender,monitor=configured
    sender.results=[StepResult('confirmed','greeting'),StepResult('unknown')]
    await monitor.poll_once(1)
    source.orders['official-1']=fact(status='WAIT_BUYER_CONFIRM_GOODS')
    result=await monitor.reconcile_record(1)
    assert result['confirmationState']=='confirmed'
    assert result['contentState']=='UNKNOWN' and result['safetyStatus']=='NEED_MANUAL_CHECK'
    assert '平台已确认' in result['warning']
    assert rows(store,'SELECT * FROM card_item WHERE id=1')[0]['status']==1
    assert len(sender.sent)==2 and not sender.confirmed


@pytest.mark.asyncio
async def test_unknown_content_blocks_B_even_if_order_is_waiting(configured):
    store,rules,source,sender,monitor=configured
    sender.results=[StepResult('unknown')]
    await monitor.poll_once(1)
    with pytest.raises(InventoryError,match='内容'):
        await monitor.reconcile_record(1,retry_confirmation=True,request_key='blocked-request')
    assert not sender.confirmed
    assert store.get_group(1,stats=True)['remainCount']==11


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'status':'TRADE_CLOSED'},{'refunding':True},{'buyer_id':'other'},
    {'quantity':2},{'item_id':'other'},{'seller_id':'other'},{'sku_id':'other'},{'amount_cent':1}])
async def test_B_retry_revalidates_official_status_and_frozen_identity(configured,change):
    store,rules,source,sender,monitor=configured
    sender.confirm_results=[StepResult('unknown')]
    await monitor.poll_once(1)
    source.orders['official-1']=fact(**change)
    with pytest.raises(InventoryError):
        await monitor.reconcile_record(1,retry_confirmation=True,request_key='unsafe-request')
    assert len(sender.confirmed)==1 and store.get_group(1,stats=True)['remainCount']==11


@pytest.mark.asyncio
async def test_startup_recovers_inflight_card_before_any_new_dispatch(configured):
    store,rules,source,sender,monitor=configured
    original=monitor.execute
    async def no_execute(did):pass
    monitor.execute=no_execute
    did=await monitor.consider(1,'official-1')
    monitor.execute=original
    greeting=store.claim_next(did)
    store.finish(greeting,StepResult('confirmed','greeting'))
    pending=store.claim_next(did)  # Crash after persisted intent, before a receipt.
    monitor.request_reconciliation(1,'system_resumed')
    await monitor.poll_once(1)
    result=store.detail(did)
    assert result['safetyStatus']=='NEED_MANUAL_CHECK' and result['contentState']=='UNKNOWN'
    assert not sender.sent and not sender.confirmed
    assert store.get_group(1,stats=True)['remainCount']==11
    with pytest.raises(InventoryError,match='过期'):
        store.finish(pending,StepResult('confirmed','late-receipt'))
    assert store.detail(did)['contentState']=='UNKNOWN'  # Stale attempt cannot resurrect.


@pytest.mark.asyncio
async def test_connection_failure_during_reconciliation_keeps_gate_closed(configured):
    store,rules,source,sender,monitor=configured
    sender.confirm_results=[StepResult('unknown')]
    await monitor.poll_once(1)
    monitor.request_reconciliation(1,'im_reconnected')
    async def offline(*args):raise TimeoutError()
    original=source.get_order
    source.get_order=offline
    with pytest.raises(TimeoutError):await monitor.poll_once(1)
    assert 1 in monitor._resume_required and len(sender.confirmed)==1
    source.get_order=original
    source.orders['official-1']=fact(status='WAIT_BUYER_CONFIRM_GOODS')
    await monitor.poll_once(1)
    assert 1 not in monitor._resume_required and store.detail(1)['workflowState']=='completed'
    assert len(sender.confirmed)==1


def test_resume_detector_catches_sleep_wall_jumps_and_long_scheduler_pause():
    detector=ResumeDetector()
    assert not detector.sample(100,10)
    assert not detector.sample(105,15)
    assert detector.sample(200,20)  # Sleep where monotonic did not advance.
    assert detector.sample(240,60)  # Sleep where both clocks advanced.
    assert detector.sample(230,65)  # Clock adjusted backwards: reverify freshness.
    assert not detector.sample(235,70)


def test_operator_boundary_and_offline_ui_are_fail_closed(store,monkeypatch,tmp_path):
    from app.core import paths
    monkeypatch.setattr(paths,'SOURCE_ROOT',store.path.parent.parent)
    web=tmp_path/'web';web.mkdir();(web/'assets').mkdir()
    (web/'desktop-safety.html').write_text('<html>发货安全工作台</html>',encoding='utf-8')
    (web/'assets'/'safety.js').write_text('export {}',encoding='utf-8')
    app=create_desktop_app(Settings(runtime_mode='desktop'),web_dir=web)
    with TestClient(app,base_url='http://127.0.0.1:15177') as client:
        assert client.get('/desktop/safety').status_code==200
        assert client.get('/assets/safety.js').status_code==200
        assert client.get('/health/ready').json()['safetyUiBuilt'] is True
        assert client.get('/api/desktop/safety/session').status_code==403
        response=client.get('/api/desktop/safety/session',headers={'X-Desktop-UI':'delivery-safety','Sec-Fetch-Site':'same-origin'})
        session=response.json()['data']
        assert not session['actionsAvailable'] and response.headers['cache-control']=='no-store'
        endpoint='/api/desktop/safety/records/1/retry-confirmation'
        assert client.post(endpoint,json={'requestId':'test-request'}).status_code==403
        assert client.post(endpoint,json={'requestId':'test-request'},headers={'X-Desktop-Action':session['token']}).status_code==503
        assert client.get('/api/desktop/safety/session',headers={'X-Desktop-UI':'delivery-safety','Sec-Fetch-Site':'cross-site'}).status_code==403
        assert session['token'] not in client.get('/health/ready').text
    assert create_desktop_app(Settings(runtime_mode='desktop')).state.safety_action_token!=session['token']


@pytest.mark.asyncio
async def test_dispatch_detects_resume_before_lifecycle_watcher_can_run(configured,monkeypatch):
    store,rules,source,sender,monitor=configured
    original=monitor.execute
    async def deferred(did):pass
    monitor.execute=deferred
    did=await monitor.consider(1,'official-1')
    monitor.execute=original
    monkeypatch.setattr(monitor.continuity,'sample',lambda:True)
    await monitor.execute(did)
    assert 1 in monitor._resume_required and not sender.sent
    assert store.detail(did)['segments'][0]['state']=='pending'


@pytest.mark.asyncio
async def test_resume_during_sqlite_claim_cannot_start_external_send(configured,monkeypatch):
    store,rules,source,sender,monitor=configured
    claim=monitor.store.claim_next
    def interrupted_claim(did):
        work=claim(did)
        # Database worker completes immediately before a system wake signal.
        monitor._resume_required[1]='system_resumed'
        return work
    monkeypatch.setattr(monitor.store,'claim_next',interrupted_claim)
    await monitor.poll_once(1)
    assert not sender.sent and not sender.confirmed
    assert store.detail(1)['segments'][0]['state']=='failed'
    assert store.detail(1)['segments'][0]['retrySafe']
    assert store.get_group(1,stats=True)['remainCount']==11


@pytest.mark.asyncio
async def test_new_resume_signal_cannot_be_cleared_by_older_reconciliation(configured):
    store,rules,source,sender,monitor=configured
    sender.confirm_results=[StepResult('unknown')]
    await monitor.poll_once(1)
    monitor.request_reconciliation(1,'startup')
    source.on_get=lambda _:monitor.request_reconciliation(1,'another_resume',wake=False)
    async with monitor.lock(1):
        assert not await monitor.ensure_reconciled(1)
    assert monitor._resume_required[1]=='another_resume'
    assert len(sender.confirmed)==1


@pytest.mark.asyncio
async def test_official_positive_B_proof_seals_inflight_attempt_against_late_ack(configured):
    store,rules,source,sender,monitor=configured
    sender.confirm_results=[StepResult('unknown')]
    await monitor.poll_once(1)
    monitor.store.grant_confirmation_retry(1,'repair-inflight')
    work=monitor.store.claim_confirmation_only(1)
    source.orders['official-1']=fact(status='WAIT_BUYER_CONFIRM_GOODS')
    # Simulate a host reconciling after a platform response was lost locally.
    monitor.store.ingest_order(source.orders['official-1'],1)
    monitor.store.reconcile_fulfillment(1)
    with pytest.raises(InventoryError,match='过期'):
        store.finish(work,StepResult('unknown'))
    assert store.detail(1)['workflowState']=='completed'
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection(write=True) as conn:
            conn.execute("UPDATE delivery_record SET confirmation_state='pending' WHERE id=1")


def test_operator_route_requires_token_validates_body_and_delegates_only_requested_scope(store,monkeypatch):
    from app.core import paths
    monkeypatch.setattr(paths,'SOURCE_ROOT',store.path.parent.parent)
    did=store.prepare(order(store),plan(image=False))
    class Runtime:
        running=False
        clients={}
        calls=[]
        async def start(self):self.running=True
        async def stop(self):self.running=False
        async def reconcile_record(self,record_id,**kwargs):
            self.calls.append((record_id,kwargs))
            return store.detail(record_id)
    runtime=Runtime()
    with TestClient(create_desktop_app(Settings(runtime_mode='desktop'),platform_runtime=runtime),base_url='http://127.0.0.1:15177') as client:
        session=client.get('/api/desktop/safety/session',headers={'X-Desktop-UI':'delivery-safety','Sec-Fetch-Site':'same-origin'}).json()['data']
        assert session['actionsAvailable']
        headers={'X-Desktop-Action':session['token']}
        endpoint=f'/api/desktop/safety/records/{did}/retry-confirmation'
        assert client.post(endpoint,json={'requestId':'too-short!'},headers=headers).status_code==422
        assert client.post(endpoint,json={'requestId':'operator-request','release':True},headers=headers).status_code==422
        result=client.post(endpoint,json={'requestId':'operator-request'},headers=headers)
        assert result.status_code==200
        assert runtime.calls==[(did,{'retry_confirmation':True,'request_key':'operator-request'})]
        assert 'password-' not in result.text and session['token'] not in result.text
