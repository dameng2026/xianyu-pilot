from __future__ import annotations

import io
import importlib
import json
import secrets
import socket
import sys
from pathlib import Path

import httpx
import jwt
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import upload_security
from app.core import cookie_crypto, security
from app.services import remote_slider_config, remote_slider_solver, ws_storage


def dns_result(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]


@pytest.mark.asyncio
@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'])
async def test_private_dns_is_rejected_before_connecting(monkeypatch, address):
    monkeypatch.setattr(upload_security.socket, 'getaddrinfo', lambda *a, **kw: dns_result(address))
    def unexpected_client(**kwargs):
        pytest.fail('Private destination reached HTTP client')
    monkeypatch.setattr(upload_security.httpx, 'AsyncClient', unexpected_client)
    with pytest.raises(upload_security.UnsafeRemoteURLError):
        await upload_security.request_public_https('https://service.example/solve', method='GET')


@pytest.mark.asyncio
async def test_https_request_pins_dns_and_rejects_redirects_and_oversize(monkeypatch):
    resolutions = []
    def resolve(*args, **kwargs):
        resolutions.append(args)
        return dns_result('8.8.8.8')
    monkeypatch.setattr(upload_security.socket, 'getaddrinfo', resolve)
    real_client = httpx.AsyncClient
    requests = []
    response_kind = ['ok']
    def handle(request):
        requests.append(request)
        if response_kind[0] == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://127.0.0.1/private'})
        if response_kind[0] == 'oversize':
            return httpx.Response(200, content=b'x' * 100)
        return httpx.Response(200, json={'ok': True})
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(upload_security.httpx, 'AsyncClient', client)
    response = await upload_security.request_public_https('https://service.example/solve', method='GET')
    assert response.json() == {'ok': True}
    assert len(resolutions) == 1
    assert requests[0].url.host == '8.8.8.8'
    assert requests[0].headers['Host'] == 'service.example'
    assert requests[0].extensions['sni_hostname'] == 'service.example'
    for kind in ['redirect', 'oversize']:
        response_kind[0] = kind
        count = len(requests)
        with pytest.raises(upload_security.UnsafeRemoteURLError):
            await upload_security.request_public_https('https://service.example/solve', method='GET', max_response_bytes=32)
        assert len(requests) == count + 1


@pytest.mark.asyncio
async def test_slider_precheck_rejects_private_and_http_targets(monkeypatch):
    monkeypatch.setattr(upload_security.socket, 'getaddrinfo', lambda *a, **kw: dns_result('127.0.0.1'))
    for url in ['https://internal.example/solve', 'http://service.example/solve']:
        result = await remote_slider_config.precheck_remote_slider(url, 'test-key')
        assert result['ok'] is False and result['reachable'] is False


@pytest.mark.asyncio
async def test_remote_solve_revalidates_dns_after_successful_precheck(monkeypatch):
    # A successful/cached precheck must not authorize a later private address.
    monkeypatch.setattr(upload_security.socket, 'getaddrinfo', lambda *a, **kw: dns_result('10.0.0.1'))
    with pytest.raises(upload_security.UnsafeRemoteURLError):
        await remote_slider_solver._request_remote_slider('https://service.example/solve', 'test-key', {'cookie': 'test'})




@pytest.mark.asyncio
async def test_commercial_image_normalizes_and_preserves_404(monkeypatch):
    output = io.BytesIO()
    Image.new('RGB', (2, 2)).save(output, format='PNG')
    async def download(*args, **kwargs):
        assert kwargs['allow_redirects'] is False
        return output.getvalue()
    monkeypatch.setattr(upload_security, 'download_public_image', download)
    data, content_type = await upload_security.download_trusted_origin_image('https://images.example/a.png')
    assert content_type == 'image/png'
    assert Image.open(io.BytesIO(data)).size == (2, 2)
    async def not_found(*args, **kwargs):
        response = httpx.Response(404, request=httpx.Request('GET', args[0]))
        response.raise_for_status()
    monkeypatch.setattr(upload_security, 'download_public_image', not_found)
    with pytest.raises(FileNotFoundError):
        await upload_security.download_trusted_origin_image('https://images.example/missing.png')




def test_cdn_host_match_rejects_embedded_domains():
    def extract(url):
        return ws_storage._extract_item_main_pic(json.dumps({'rawPayload': {'sessionInfo': {
            'extensions': {'other': url}}}}))
    assert extract('https://img.alicdn.com/a') == 'https://img.alicdn.com/a'
    for url in ['https://alicdn.com.attacker.example/a', 'https://attacker.example/alicdn.com',
                'https://img.taobao.attacker.example/a']:
        assert extract(url) == ''


@pytest.mark.parametrize('module,function', [
    ('ws_token', '_make_sign'), ('xianyu_api_service', '_make_sign'), ('xianyu_goods_sync', '_build_sign'),
])
def test_reviewed_mtop_signatures_preserve_protocol_vector(module, function):
    sign = getattr(importlib.import_module('app.services.' + module), function)
    assert sign('test-token', 1700000000000, '{}') == '102912d3193c188a8a556546a3cd9088'


def test_updated_crypto_and_jwt_dependencies_preserve_authentication(monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setattr(security.settings, 'jwt_secret', secret)
    token = security.create_token('test-admin')
    assert security.decode_token(token)['username'] == 'test-admin'
    with pytest.raises(jwt.PyJWTError):
        security.decode_token(token.rsplit('.', 1)[0] + '.invalid')
    monkeypatch.setattr(cookie_crypto, '_get_secret', lambda: secret)
    encrypted = cookie_crypto.encrypt_cookie_for_storage('test-cookie=value')
    assert cookie_crypto.decrypt_cookie_if_needed(encrypted) == 'test-cookie=value'
    monkeypatch.setattr(cookie_crypto, '_get_secret', lambda: secrets.token_hex(32))
    with pytest.raises(RuntimeError):
        cookie_crypto.decrypt_cookie_if_needed(encrypted)
