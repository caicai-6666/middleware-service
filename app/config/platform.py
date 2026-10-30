"""从 YAML 加载平台登记配置，在启动时形成内存快照。"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path("data/platform-config/platform.yaml")


class PlatformConfigError(ValueError):
    """不包含配置原文或密钥的启动错误。"""


class Platform(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", description="平台唯一代号。")
    name: str = Field(min_length=1, description="平台中文展示名称。")
    description: str = Field(min_length=1, description="平台用途说明，不参与权限判定。")
    secret: SecretStr = Field(exclude=True, repr=False, description="YAML 中配置的平台密钥，不对外序列化。")
    supports_a2a: bool = Field(default=False, description="是否参与 A2A 协议；平台 A2A 子平台接口要求此标记为 true，不改变平台登录及其他消息流权限。")
    is_sub_management_platform: bool = Field(default=False, description="是否为子管理平台；平台 A2A 子平台接口要求此标记为 true，不改变平台登录及其他消息流权限。")
    is_management_entry: bool = Field(default=False, description="是否作为管理平台入口，独立于子管理平台标记，不参与当前身份或流权限判断。")
    a2aresponsibility: str = Field(default="", description="平台在 A2A 协作中的职责；子管理平台必须填写非空且非纯空白的字符串，其他平台可省略，默认为空字符串。")
    a2a_max_inflight: int | None = Field(default=None, strict=True, ge=1, le=100, description="本平台 A2A 消费窗口，子管理平台用于请求，顶层用于单聊反馈；1～100 的严格整数，省略或 null 时使用服务默认值，领取自动补齐剩余名额。")

    @model_validator(mode="after")
    def require_sub_platform_responsibility(self):
        # 按子管理平台身份强制声明职责，不以 supports_a2a 开关绕过该约束。
        if self.is_sub_management_platform and not self.a2aresponsibility.strip():
            raise ValueError("子管理平台必须填写非空的 a2aresponsibility。")
        return self



class _PlatformFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    platforms: list[Platform] = Field(min_length=1)


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        # 默认 YAML 会静默覆盖重复字段，身份配置必须拒绝这种歧义。
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise PlatformConfigError("平台配置存在重复或非字符串字段名。")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_platforms(path: Path) -> dict[str, Platform]:
    # 相对路径始终以项目根目录为基准，不依赖终端或 IDE 的工作目录。
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError):
        # YAML 和校验库的原始异常可能包含输入值，禁止直接暴露。
        raise PlatformConfigError("平台配置文件无法读取或 YAML 格式错误。") from None

    if not isinstance(data, dict) or not isinstance(data.get("platforms"), list):
        raise PlatformConfigError("平台配置必须包含 platforms 列表。")

    for index, item in enumerate(data["platforms"], start=1):
        if not isinstance(item, dict):
            raise PlatformConfigError(f"第 {index} 个平台必须为对象。")
        value = item.get("secret")
        if not isinstance(value, str) or not value.strip():
            raise PlatformConfigError(f"第 {index} 个平台的 secret 必须是非空字符串。")
        item["secret"] = SecretStr(value)

    try:
        entries = _PlatformFile.model_validate(data).platforms
    except ValidationError:
        raise PlatformConfigError("平台字段校验失败：请检查必填字段、类型、代号格式与未知字段。") from None

    platforms = {}
    secrets = set()
    for entry in entries:
        if not entry.name.strip() or not entry.description.strip():
            raise PlatformConfigError("平台名称和用途描述不能仅包含空白字符。")
        if entry.code in platforms:
            raise PlatformConfigError("平台代号不能重复。")
        if entry.secret.get_secret_value() in secrets:
            raise PlatformConfigError("不同平台不能共用密钥。")
        platforms[entry.code] = entry
        secrets.add(entry.secret.get_secret_value())
    return platforms


def load_platforms_from_env() -> dict[str, Platform]:
    path = os.environ.get("PLATFORM_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)).strip()
    if not path:
        raise PlatformConfigError("PLATFORM_CONFIG_PATH 不能设置为空。")
    return load_platforms(Path(path))
