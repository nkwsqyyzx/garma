"""
测试 RPC 只读代理端点。

通过 FastAPI TestClient 测试所有 /rpc/* 读取端点。
直接注入本地 MPShared 实例，使用 _test_ 前缀的 key，测试完毕后清理。
"""

import sys
import pytest
from pathlib import Path

# 添加项目路径
_HERE = Path(__file__).resolve().parent
_QMT_MARKET = _HERE.parent
_GARMA_ROOT = _QMT_MARKET.parent
sys.path.insert(0, str(_GARMA_ROOT))
sys.path.insert(0, str(_QMT_MARKET))

from fastapi.testclient import TestClient

from rpc.multiprocess_shared_base import MPShared

# ---------------------------------------------------------------------------
# 共享实例 & 预置测试数据
# ---------------------------------------------------------------------------

_the_shared = MPShared()

# 预置一些数据用于只读测试
_the_shared.set("_test_kv_str", "hello")
_the_shared.set("_test_kv_dict", {"a": [1, 2, 3]})
_the_shared.hset("_test_hash", "f1", "val1")
_the_shared.hset("_test_hash", "f2", 42)
_the_shared.rpush("_test_list", "a")
_the_shared.rpush("_test_list", "b")
_the_shared.sadd("_test_set", "x")
_the_shared.sadd("_test_set", "y")


@pytest.fixture(autouse=True)
def inject_shared():
    """每个测试前注入 MPShared 实例。"""
    import api.rpc as rpc_mod
    rpc_mod._mp_shared = _the_shared
    yield


@pytest.fixture
def client():
    from main import app
    with TestClient(app) as c:
        yield c


# ===========================================================================
# 测试用例
# ===========================================================================

class TestKeys:
    def test_keys(self, client):
        keys = client.get("/rpc/keys").json()["data"]
        assert "_test_kv_str" in keys
        assert "_test_kv_dict" in keys


class TestKV:
    def test_get_string(self, client):
        r = client.get("/rpc/kv/_test_kv_str")
        assert r.json()["data"] == "hello"

    def test_get_dict(self, client):
        r = client.get("/rpc/kv/_test_kv_dict")
        assert r.json()["data"] == {"a": [1, 2, 3]}

    def test_get_missing_key(self, client):
        r = client.get("/rpc/kv/_test_nonexistent", params={"default": "fallback"})
        assert r.json()["data"] == "fallback"


class TestHash:
    def test_hget(self, client):
        r = client.get("/rpc/hash/_test_hash/f1")
        assert r.json()["data"] == "val1"

    def test_hget_numeric(self, client):
        r = client.get("/rpc/hash/_test_hash/f2")
        assert r.json()["data"] == 42

    def test_hget_missing_field(self, client):
        r = client.get("/rpc/hash/_test_hash/missing", params={"default": "none"})
        assert r.json()["data"] == "none"


class TestSet:
    def test_smembers(self, client):
        members = client.get("/rpc/set/_test_set").json()["data"]
        assert sorted(members) == ["x", "y"]
