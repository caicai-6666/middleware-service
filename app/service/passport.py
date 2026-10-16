"""通行证统一为字符串列表，兼容历史单字符串消息。"""


class InvalidPassportError(ValueError):
    """通行证集合或元素不符合约定。"""


def normalize_passports(value: str | list[str], *, required: bool = False) -> list[str]:
    # 旧审核结果以空字符串表示未签发，统一转换为空数组。
    if isinstance(value, str):
        value = [value] if value else []
    if not isinstance(value, list):
        raise InvalidPassportError("passport 必须为字符串数组，兼容单个字符串")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise InvalidPassportError("passport 每项必须为非空白字符串")
    if required and not value:
        raise InvalidPassportError("删除申请至少需要一个有效通行证")
    # 不裁剪、不去重，保留业务值与顺序；复制列表以隔离调用方后续修改。
    return list(value)


def normalize_message_passports(message: dict) -> dict:
    if "passport" not in message:
        return message
    # 只规范返回数据，不改写 broker 历史消息或改变消费 offset。
    return {**message, "passport": normalize_passports(message["passport"])}
