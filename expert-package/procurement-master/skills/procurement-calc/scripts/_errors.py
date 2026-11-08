
# 本文件由「采购专家引擎」自动生成，请勿手改。
# 算法与系统内完全一致（同源生成）；如需调整，请修改上游引擎后重新生成。

"""业务异常与 HTTP 状态映射。

═══ 为什么要有这个模块 ═══

原来路由层用**子串猜测**决定状态码：

    status = 404 if "不存在" in str(e) else 400

这是错的，而且错法很隐蔽。实测踩到：
    客户端传了一个叫「不存在的键」的配置键，
    错误消息是「未知配置项：不存在的键。」——
    消息里带"不存在"，于是被当成"资源不存在"返回 404。

也就是说：**任何含"不存在"三个字的用户输入都会翻转状态码**。
状态码是接口契约的一部分，不能让用户输入影响它。

正确做法是**由异常类型决定**，不由消息文本决定：
    NotFoundError    → 404
    BusinessError    → 400（或异常自带的 status）
"""
from __future__ import annotations


class BusinessError(ValueError):
    """业务规则不满足 → 400（默认）。

    各领域服务的错误类（ContractError / SalesError / StockError …）都继承 ValueError，
    也都可以继承它来显式声明状态码。
    """
    status = 400
    code = "invalid"


class NotFoundError(BusinessError):
    """资源不存在 → 404。

    用法：`raise NotFoundError("客户不存在")`
    —— 状态码来自类型，与消息文本无关。
    """
    status = 404
    code = "not_found"


def http_status_of(exc: BaseException) -> int:
    """从异常推断 HTTP 状态码。**只认类型与显式声明，不做文本匹配。**"""
    st = getattr(exc, "status", None)
    if isinstance(st, int) and 400 <= st <= 599:
        return st
    if isinstance(exc, NotFoundError):
        return 404
    return 400


def code_of(exc: BaseException) -> str:
    return getattr(exc, "code", None) or "invalid"
