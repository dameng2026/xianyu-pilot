"""Run user-supplied extraction patterns in a bounded, disposable process."""
import json
import subprocess
import sys


PROGRAM = """import json,re,sys
p=json.load(sys.stdin)
try:
 m=(re.fullmatch if p.get('complete') else re.search)(p['pattern'],p['text'])
 out=None if m is None else (m.groupdict() or {'value':m.group(1) if m.lastindex else m.group(0)})
 print(json.dumps({'values':out},ensure_ascii=False))
except (re.error,IndexError):
 print(json.dumps({'error':True}))
"""


def extract(pattern: str, text: str, *, complete: bool = False) -> dict | None:
    if len(pattern) > 200 or len(text) > 8000:
        raise ValueError("提取内容超过限制，请人工确认")
    try:
        result = subprocess.run([sys.executable, "-I", "-c", PROGRAM],
                                input=json.dumps({"pattern": pattern, "text": text, "complete": complete}),
                                text=True, encoding="utf-8", capture_output=True, timeout=1, check=True)
        data = json.loads(result.stdout)
        if data.get("error"):
            raise ValueError("提取规则无效")
        values = data["values"]
        if values and any(value is None or len(value) > 2000 for value in values.values()):
            raise ValueError("提取结果缺失或过长，请人工确认")
        return values
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError("正则提取超时或失败，请人工确认") from exc
