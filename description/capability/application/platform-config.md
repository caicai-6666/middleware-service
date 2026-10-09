# 平台身份配置

本模块参照合同管理项目的用户 YAML 加载方式：文件直接保存平台信息与密钥，应用启动时读取一次并形成内存快照。登录服务使用此配置验证平台密钥，见[平台会话服务](platform-session.md)；角色授权尚未实现。

## 文件位置与结构

本地默认读取项目根目录的 `data/platform-config/platform.yaml`，无需设置环境变量或启动 Docker Compose。可通过 `PLATFORM_CONFIG_PATH` 覆盖文件路径；相对路径始终以项目根目录为基准，与终端和 IDE 工作目录无关。显式设置为空路径视为配置错误。

部署项目维护 [platform.yaml.example](../../../../middleware-service-deploy/platform.yaml.example)，示例结构如下：

```yaml
platforms:
  - code: registry
    name: 注册中心
    description: 消费入库请求并返回审核结果。
    secret: "change-this-registry-key"
```

| 字段 | 约束 |
| --- | --- |
| `code` | 全局唯一，小写字母开头，后续为小写字母、数字或分隔段落的单个连字符。 |
| `name` | 非空字符串，用于平台中文展示名称。 |
| `description` | 非空字符串，描述用途，不参与权限判断。 |
| `secret` | 直接填写非空字符串，不同平台不能共用密钥；不执行环境变量替换。 |

四个字段均必填，拒绝未知字段、重复 YAML 字段和空平台列表。名称、描述和密钥不能仅包含空白。密钥建议加引号，避免 YAML 将纯数字等内容识别为其他类型。示例密钥仅用于说明，部署时替换为各平台独立密钥。

---

## 本地使用

在 `data/platform-config/platform.yaml` 中填写平台信息后，从应用项目根目录直接启动：

```bash
conda activate middleware-service
python -m app.main
```

需要改用其他文件时：

```bash
PLATFORM_CONFIG_PATH=/absolute/path/platform.yaml python -m app.main
```

平台 YAML 路径不从 `.env` 读取；会话设置另按[平台会话服务](platform-session.md)读取根目录 `.env`。本地默认配置无需 `.env` 或逐平台密钥环境变量；如曾设置 `PLATFORM_CONFIG_PATH`，应确认其指向正确文件，或先执行 `unset PLATFORM_CONFIG_PATH` 恢复默认路径。

---

## 部署挂载

部署侧复制 `platform.yaml.example` 为 `platform.yaml` 并填写实际配置。Compose 只读挂载文件，通过环境变量覆盖后端读取路径：

```text
部署目录/platform.yaml → 只读挂载 → /data/platform-config/platform.yaml
PLATFORM_CONFIG_PATH=/data/platform-config/platform.yaml → 后端加载文件
```

配置不写入镜像，不需要逐项注入密钥变量。缺少源文件时挂载配置不会自动创建同名目录。在部署项目目录执行：

```bash
docker compose --profile backend up -d --build --wait
```

Compose 的 `.env` 负责 RabbitMQ 凭据和构建路径等部署参数。本机同级仓库布局设置 `MIDDLEWARE_BUILD_CONTEXT=../middleware-service`；生产预期为 `./middleware-service`。`backend` profile 不影响开发 RabbitMQ 的独立启动。

---

## 启动与运行边界

[配置加载器](../../../app/config/platform.py)使用 PyYAML 安全加载器和 Pydantic 校验。FastAPI lifespan 在接受请求前完成加载，文件缺失、格式错误、重复代号或密钥等问题都会阻止启动；错误信息不包含配置原文或密钥。

每个平台转为不可变 `Platform` 结构体，以代号索引存入只读的 `application.state.platforms`。后续服务可通过 `request.app.state.platforms[platform_code]` 访问。密钥使用 `SecretStr`，从模型序列化和对象表示中排除，需要时显式调用 `secret.get_secret_value()`。

配置修改后需重启应用，不热加载。本地实际配置已加入 `.gitignore`，Docker 构建也排除实际 `platform.yaml`。部署侧实际配置同样不提交仓库；文件由操作系统权限控制，容器运行用户须具备只读权限。

本模块不签发 token、不建立 RabbitMQ 连接；`registry` 仅为示例代号，角色权限需在后续认证模块中定义。

---

## 验证

[配置测试](../../../tests/test_platform_config.py)覆盖默认与覆盖路径、工作目录独立性、直接密钥、字段与重复项校验、敏感信息保护及启动生命周期。

```bash
conda activate middleware-service
python -m unittest discover -s tests -v
```
