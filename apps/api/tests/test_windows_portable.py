"""Portable lifecycle and storage tests without installing a GUI locally."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import shutil
import stat
import sys
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from test_desktop_delivery import store,order,plan,rows,Gateway
from test_official_delivery import configured,fact
from app.core import paths
from app.core.config import Settings
from app.core.instance_lock import SingleInstance
from app.desktop_app import create_desktop_app
from app.portable.bootstrap import configure_environment,bundled_web_dir,portable_self_check
from app.portable.backend import DesktopBackend
from app.portable.windows_events import power_event
from app.services.delivery_gate import DeliveryGate
from app.services.desktop_delivery import DesktopDeliveryCoordinator
from app.services.desktop_delivery_contracts import StepResult
from app.services.official_delivery_gateway import OfficialDeliveryRuntime


@pytest.fixture
def portable(tmp_path,monkeypatch):
    root=tmp_path/'便携 程序'
    root.mkdir()
    monkeypatch.setattr(paths,'SOURCE_ROOT',root)
    # Restore every process-global bootstrap change at the end of the test.
    keys=['RUNTIME_MODE','APPDATA','LOCALAPPDATA','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME',
          'QML_DISK_CACHE_PATH','QTWEBENGINE_CHROMIUM_FLAGS','QTWEBENGINE_REMOTE_DEBUGGING',
          'QTWEBENGINE_DISABLE_SANDBOX','TEMP','TMP','TMPDIR','PYTHONDONTWRITEBYTECODE']
    for key in keys:
        monkeypatch.setenv(key,os.environ.get(key,''))
    monkeypatch.setattr(sys,'dont_write_bytecode',sys.dont_write_bytecode)
    import tempfile
    monkeypatch.setattr(tempfile,'tempdir',tempfile.tempdir)
    return root


def test_qt_and_chromium_all_paths_set_before_import_no_user_fallback(portable,monkeypatch):
    monkeypatch.setenv('QTWEBENGINE_CHROMIUM_FLAGS','--user-data-dir=C:/Users/unsafe --no-sandbox')
    monkeypatch.setenv('QTWEBENGINE_REMOTE_DEBUGGING','9222')
    monkeypatch.setenv('QTWEBENGINE_DISABLE_SANDBOX','1')
    root,mapping=configure_environment()
    assert root==portable/'data'
    for key in ('APPDATA','LOCALAPPDATA','QML_DISK_CACHE_PATH','TEMP','TMP','TMPDIR'):
        assert Path(os.environ[key]).is_relative_to(root)
    assert all(path.is_relative_to(root) and path.is_dir() for path in mapping.values())
    assert 'C:/Users' not in os.environ['QTWEBENGINE_CHROMIUM_FLAGS']
    assert '--no-sandbox' not in os.environ['QTWEBENGINE_CHROMIUM_FLAGS']
    assert 'QTWEBENGINE_REMOTE_DEBUGGING' not in os.environ
    assert 'QTWEBENGINE_DISABLE_SANDBOX' not in os.environ


def test_resource_bundle_is_read_only_meipass_but_data_is_always_next_to_exe(portable,monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(portable/'XianyuPilot.exe'))
    monkeypatch.setattr(sys,'_MEIPASS',str(portable/'_internal'),raising=False)
    assert bundled_web_dir()==portable/'_internal/web'
    assert paths.data_directory()==portable/'data'


def test_portable_self_check_survives_source_directory_relocation(portable,monkeypatch):
    root,_=configure_environment()
    web=portable/'web';web.mkdir();(web/'desktop-safety.html').write_text('<html/>');(web/'assets').mkdir()
    first=portable_self_check(root,web)
    moved=portable.parent/'移动后的程序'
    shutil.copytree(portable,moved)
    monkeypatch.setattr(paths,'SOURCE_ROOT',moved)
    moved_root,_=configure_environment()
    second=portable_self_check(moved_root,moved/'web')
    assert first['storageToken']==second['storageToken']
    assert second['schema']['current'] and not second['automaticDeliveryEnabled']
    assert second['dataRoot']==str(moved/'data')


def test_native_power_messages_are_specific_and_suspend_is_not_resume():
    assert power_event(0x218,4)=='suspend'
    assert power_event(0x218,7)==power_event(0x218,18)=='resume'
    assert power_event(0x218,10) is None
    assert power_event(0x123,4) is None


def test_python311_reparse_point_cannot_redirect_the_data_root(portable,monkeypatch):
    root=portable/'data'
    original=Path.lstat
    def native_attributes(path,*args,**kwargs):
        if path==root:
            return SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',native_attributes)
    with pytest.raises(ValueError,match='链接'):
        paths.contained_data_path(root,'app.sqlite3')
    assert not root.exists()


def test_gate_keeps_manual_pause_when_network_or_system_recovers():
    gate=DeliveryGate()
    gate.block('manual');gate.block('network');gate.block('suspend')
    gate.clear('network');gate.clear('suspend')
    assert gate.reasons==('manual',) and gate.blocked
    gate.clear('manual')
    assert not gate.blocked


@pytest.mark.asyncio
async def test_queued_old_resume_cannot_override_a_new_pause(configured):
    store,rules,source,sender,monitor=configured
    runtime=OfficialDeliveryRuntime(monitor,{}, {})
    backend=DesktopBackend(SimpleNamespace(),None,None,platform_runtime=runtime)
    backend.app=SimpleNamespace(state=SimpleNamespace(ready=True))
    backend.loop=asyncio.get_running_loop()
    backend.pause('manual')
    backend.resume('manual')  # Queue resume before a newer pause gets clicked.
    backend.pause('manual')
    await asyncio.sleep(.01)
    assert 'manual' in monitor.gate.reasons
    assert not sender.sent


@pytest.mark.asyncio
async def test_pause_blocks_all_automatic_and_operator_mutations(configured):
    store,rules,source,sender,monitor=configured
    monitor.gate.block('manual')
    await monitor.poll_once(1)
    assert not source.lookups and not sender.sent and not rows(store,'SELECT * FROM delivery_record')
    monitor.gate.clear('manual')
    sender.confirm_results=[StepResult('unknown')]
    await monitor.poll_once(1)
    sent=len(sender.sent)
    monitor.gate.block('manual')
    with pytest.raises(Exception,match='暂停'):
        await monitor.reconcile_record(1,retry_confirmation=True,request_key='paused-request')
    assert len(sender.sent)==sent and len(sender.confirmed)==1


@pytest.mark.asyncio
async def test_pause_arriving_after_claim_is_provably_unsent_and_keeps_stock(configured,monkeypatch):
    store,rules,source,sender,monitor=configured
    original=monitor.store.claim_next
    def paused(did):
        work=original(did);monitor.gate.block('suspend');return work
    monkeypatch.setattr(monitor.store,'claim_next',paused)
    await monitor.poll_once(1)
    assert not sender.sent and not sender.confirmed
    assert store.detail(1)['segments'][0]['retrySafe']
    assert store.get_group(1,stats=True)['remainCount']==11


@pytest.mark.asyncio
async def test_resume_requires_official_reconciliation_and_preserves_other_pause_causes(configured):
    store,rules,source,sender,monitor=configured
    runtime=OfficialDeliveryRuntime(monitor,{}, {})
    runtime.pause('manual');runtime.pause('network')
    await runtime.resume('network')
    assert monitor.gate.reasons==('manual',)
    runtime.clients={1:SimpleNamespace()}
    runtime.pause('suspend')
    await runtime.resume('suspend')
    assert 1 in monitor._resume_required
    await runtime.resume('manual')
    assert not monitor.gate.blocked and 1 in monitor._resume_required
    assert not sender.sent


@pytest.mark.asyncio
async def test_tray_never_reports_green_without_connected_verified_accounts(configured):
    store,rules,source,sender,monitor=configured
    runtime=OfficialDeliveryRuntime(monitor,{}, {})
    runtime.running=True
    assert (await runtime.desktop_status())['state']=='paused'
    runtime.clients={1:SimpleNamespace()}
    assert (await runtime.desktop_status())['state']=='attention'
    monitor.store.monitor_state(1,'monitoring','verified')
    assert (await runtime.desktop_status())['state']=='running'
    monitor.request_reconciliation(1,'wake')
    assert (await runtime.desktop_status())['state']=='reconciling'
    runtime.pause()
    assert (await runtime.desktop_status())['state']=='paused'


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_delivery_commit_instead_of_cancelling_it(configured):
    store,rules,source,sender,monitor=configured
    original=monitor.execute
    async def deferred(did):pass
    monitor.execute=deferred
    did=await monitor.consider(1,'official-1')
    monitor.execute=original
    started=asyncio.Event();ack=asyncio.Event()
    class Client:
        stopped=False
        async def stop(self):self.stopped=True
    client=Client()
    runtime=OfficialDeliveryRuntime(monitor,{1:client},{1:'seller-1'})
    runtime.running=True
    async def worker():
        async with monitor.lock(1):
            work=await asyncio.to_thread(store.claim_next,did)
            started.set()
            await ack.wait()
            await asyncio.to_thread(store.finish,work,StepResult('confirmed','ack-before-shutdown'))
    task=asyncio.create_task(worker())
    await started.wait()
    closing=asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    assert not closing.done() and monitor.gate.blocked
    ack.set()
    await asyncio.wait_for(asyncio.gather(task,closing),3)
    assert client.stopped and store.detail(did)['segments'][0]['state']=='confirmed'
    assert store.get_group(1,stats=True)['remainCount']==11


@pytest.mark.asyncio
async def test_early_wake_is_remembered_until_backend_ready():
    backend=DesktopBackend(SimpleNamespace(),None,None)
    backend.pause('suspend')
    assert not backend.resume('suspend')
    backend.app=SimpleNamespace(state=SimpleNamespace(ready=True))
    backend.loop=asyncio.get_running_loop()
    backend.poll()
    await asyncio.sleep(.01)
    assert 'suspend' not in backend.gate.reasons
    assert not backend.resume('shutdown')


def test_ten_concurrent_orders_cannot_oversell_and_unknown_never_releases(store):
    ids=[order(store,f'acceptance-{i}') for i in range(10)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        dids=list(pool.map(lambda oid:store.prepare(oid,plan(image=False)),ids))
    assert len(set(dids))==10
    cards=rows(store,'SELECT * FROM delivery_card_reservation')
    assert len(cards)==10 and len({c['card_id'] for c in cards})==10
    for did in dids:
        work=store.claim_next(did)
        store.finish(work,StepResult('unknown'))
    store.recover_interrupted()
    assert store.get_group(1,stats=True)['remainCount']==2
    assert all(store.detail(did)['safetyStatus']=='NEED_MANUAL_CHECK' for did in dids)


def test_acceptance_three_cards_one_tutorial_and_link_follow_order(store):
    did=store.prepare(order(store,quantity=3),plan())
    gateway=Gateway()
    result=DesktopDeliveryCoordinator(store,gateway).execute(did)
    assert result['workflowState']=='completed'
    assert [work.kind for work in gateway.calls]==['text','card','card','card','text','image','link']
    assert len({work.payload['content'] for work in gateway.calls if work.kind=='card'})==3


def test_backend_borrows_a_live_host_lock_but_rejects_a_fake_unlock(store,monkeypatch):
    monkeypatch.setattr(paths,'SOURCE_ROOT',store.path.parent.parent)
    config=Settings(runtime_mode='desktop')
    with pytest.raises(ValueError,match='实例锁'):
        create_desktop_app(config,instance_lock=SingleInstance(store.path.parent))
    with SingleInstance(store.path.parent) as lock:
        with TestClient(create_desktop_app(config,instance_lock=lock),base_url='http://127.0.0.1:15177') as client:
            assert client.get('/health/ready').status_code==200
            with pytest.raises(RuntimeError,match='已有'):
                with SingleInstance(store.path.parent):pass


def test_local_backend_thread_stops_and_releases_connections_before_host_lock(store,monkeypatch,tmp_path):
    monkeypatch.setattr(paths,'SOURCE_ROOT',store.path.parent.parent)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    web=tmp_path/'web';web.mkdir()
    with SingleInstance(store.path.parent) as lock:
        backend=DesktopBackend(Settings(runtime_mode='desktop',desktop_port=port),web,lock)
        backend.start()
        try:
            deadline=time.monotonic()+10
            while not backend.ready and not backend.error and time.monotonic()<deadline:
                time.sleep(.01)
            assert backend.ready and not backend.error
            assert backend.poll()['state']!='running'  # Default runtime is offline.
            backend.pause('manual')
            assert backend.poll()['state']=='paused'
            backend.stop()
            backend.thread.join(10)
            assert backend.stopped and not backend.error
        finally:
            backend.stop();backend.thread.join(10)
            assert backend.stopped
