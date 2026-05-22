"""企业微信群机器人消息发送。

用法:
    from backend.utils.wechat import send_text, send_markdown

    # 纯文本
    await send_text("订单已成交")

    # Markdown（支持 @人）
    await send_markdown("## 成交通知\n> 510310.SH 买入100股")
"""

import httpx
from loguru import logger

from backend.config import get_settings

_BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="

# webhook key，从 Settings 注入
_webhook_key: str = get_settings().WECHAT_WEBHOOK_KEY


def init_wechat(webhook_key: str) -> None:
    """初始化 webhook key，应在应用启动时调用。"""
    global _webhook_key
    _webhook_key = webhook_key


def _url() -> str:
    if not _webhook_key:
        raise ValueError("wechat webhook key not configured, call init_wechat() first")
    return _BASE_URL + _webhook_key


async def _send(data: dict) -> bool:
    """发送消息到企业微信群机器人。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_url(), json=data)
            result = resp.json()
            if result.get("errcode") == 0:
                return True
            logger.warning("Wechat send failed: errcode={} errmsg={}", result.get("errcode"), result.get("errmsg"))
            return False
    except Exception as e:
        logger.error("Wechat send error: {}", e)
        return False


async def send_text(content: str, mentioned_list: list[str] | None = None,
                    mentioned_mobile_list: list[str] | None = None) -> bool:
    """发送文本消息。

    Args:
        content: 文本内容
        mentioned_list: @的 userid 列表，["@all"] @所有人
        mentioned_mobile_list: @的手机号列表
    """
    return await _send({
        "msgtype": "text",
        "text": {
            "content": content,
            "mentioned_list": mentioned_list or [],
            "mentioned_mobile_list": mentioned_mobile_list or [],
        },
    })


async def send_markdown(content: str) -> bool:
    """发送 Markdown 消息。"""
    return await _send({
        "msgtype": "markdown",
        "markdown": {"content": content},
    })
