"""Current Windows user's DPAPI. No plaintext or machine-wide fallback."""
import ctypes
from ctypes import wintypes
import json
import os


class CredentialError(ValueError):
    pass


class WindowsVault:
    def _transform(self, value: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise CredentialError("真实凭据保存需要 Windows DPAPI；当前平台只支持模拟或无凭据的手动模式")

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

        buffer = ctypes.create_string_buffer(value)
        source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        target = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            code = ctypes.get_last_error()
            raise CredentialError(f"Windows 凭据保护失败（系统错误 {code}）；请使用原 Windows 用户和原数据目录")
        try:
            return ctypes.string_at(target.data, target.size)
        finally:
            kernel32.LocalFree(target.data)

    def seal(self, context: str, value: str) -> bytes:
        return self._transform(json.dumps({"context": context, "value": value}).encode(), decrypt=False)

    def open(self, context: str, value: bytes) -> str:
        try:
            payload = json.loads(self._transform(value, decrypt=True))
            if payload["context"] != context:
                raise CredentialError("凭据归属不匹配")
            return payload["value"]
        except (ValueError, KeyError) as exc:
            raise CredentialError("凭据无法解锁，请使用原 Windows 用户重新绑定") from exc
