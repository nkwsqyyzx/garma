"""
代理rpc/multiprocess_shared.py中MPShared接口的HTTP只读代理。

通过 BaseManager TCP 连接到共享内存服务，将 MPShared 读取方法暴露为 REST 端点。
"""

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rpc", tags=["rpc"])


class RpcResponse(BaseModel):
    """RPC 专用响应格式，data 支持任意类型。"""
    code: int = Field(0, description="0=成功，非0=错误")
    msg: str = Field("ok", description="错误时为错误描述")
    data: Any = Field(None, description="业务数据")


# ---------------------------------------------------------------------------
# MPShared 连接（懒加载单例）
# ---------------------------------------------------------------------------

_mp_shared = None


def _get_shared():
    """获取 MPShared 实例（首次调用时连接 BaseManager 服务）。"""
    global _mp_shared
    if _mp_shared is not None:
        return _mp_shared
    # noinspection PyUnresolvedReferences
    from rpc.multiprocess_shared import get_global_mp_shared
    _mp_shared = get_global_mp_shared("127.0.0.1", 50000)
    return _mp_shared


def _json_safe(obj):
    """将 DataFrame 等非 JSON 类型转为可序列化的 Python 对象（递归处理嵌套结构）。"""
    if obj is None:
        return None
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient="records")
        if isinstance(obj, pd.Series):
            return obj.tolist()
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# 通用 KV
# ---------------------------------------------------------------------------

@router.get("/keys", summary="获取所有键")
async def rpc_keys():
    inst = _get_shared()
    data = await asyncio.to_thread(inst.keys)
    return RpcResponse(data=data)


@router.get("/kv/{key:path}", summary="读取键值")
async def rpc_get(key: str, default: Optional[str] = Query(None)):
    inst = _get_shared()
    data = await asyncio.to_thread(inst.get, key, default)
    return RpcResponse(data=_json_safe(data))


# ---------------------------------------------------------------------------
# Hash
# ---------------------------------------------------------------------------

@router.get("/hash/{key:path}/{field}", summary="读取 Hash 字段")
async def rpc_hget(key: str, field: str, default: Optional[str] = Query(None)):
    inst = _get_shared()
    data = await asyncio.to_thread(inst.hget, key, field, default)
    return RpcResponse(data=_json_safe(data))


# ---------------------------------------------------------------------------
# Set
# ---------------------------------------------------------------------------

@router.get("/set/{key:path}", summary="获取 Set 成员")
async def rpc_smembers(key: str):
    inst = _get_shared()
    data = await asyncio.to_thread(inst.smembers, key)
    return RpcResponse(data=data)
