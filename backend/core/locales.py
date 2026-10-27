"""请求级语言契约。

只支持 ``en`` 与 ``zh-CN``。缺省值固定为 ``zh-CN``，以兼容不传 locale 的旧客户端。
语言必须随请求传递，不能保存在进程级可变全局中。
"""

SUPPORTED_LOCALES = ("en", "zh-CN")
DEFAULT_LOCALE = "zh-CN"

_INVALID_MESSAGE = "Unsupported locale; use 'en' or 'zh-CN' / 不支持的语言，仅支持 en 或 zh-CN"


class LocaleError(ValueError):
    """调用方提供了不受支持的语言值。"""


def normalize_locale(value, default: str = DEFAULT_LOCALE) -> str:
    """校验并规范化语言值；空值使用缺省语言，非法值抛出 LocaleError。"""
    if value is None:
        return default
    if not isinstance(value, str):
        raise LocaleError(_INVALID_MESSAGE)
    value = value.strip()
    if not value:
        return default
    if value not in SUPPORTED_LOCALES:
        raise LocaleError(_INVALID_MESSAGE)
    return value


def pick(locale: str, en: str, zh: str) -> str:
    """按语言选择文案；未知值按缺省中文处理。"""
    return en if locale == "en" else zh
