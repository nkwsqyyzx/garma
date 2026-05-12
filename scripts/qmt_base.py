"""QMT 公共 HTTP 请求模块"""
import json
import sys
import urllib.request
import urllib.error

BASE_URL = "http://192.168.1.11:3300"
TIMEOUT = 10


def api_get(path, params=None):
    """通用 GET 请求，返回解析后的 JSON 字典。失败时打印错误并 sys.exit(1)。"""
    url = BASE_URL + path
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url += ("?" if "?" not in path else "&") + query
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"网络错误: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"请求失败: {e}", file=sys.stderr)
        sys.exit(1)


def api_post(path, data=None):
    """通用 POST 请求，返回解析后的 JSON 字典。失败时打印错误并 sys.exit(1)。"""
    url = BASE_URL + path
    body = json.dumps(data or {}).encode()
    try:
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"网络错误: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"请求失败: {e}", file=sys.stderr)
        sys.exit(1)


def print_result(data):
    """统一输出 JSON 结果"""
    print(json.dumps(data, ensure_ascii=False, indent=2))
