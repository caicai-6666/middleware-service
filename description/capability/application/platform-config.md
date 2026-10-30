# 平台身份配置

本模块参照合同管理项目的用户 YAML 加载方式：文件直接保存平台信息与密钥，应用启动时读取一次并形成内存快照。登录服务使用此配置验证平台密钥，见[平台会话服务](platform-session.md)；平台角色和业务授权由相应服务校验。

## 文件位置与结构

本地默认读取项目根目录的 `data/platform-config/platform.yaml`，无需设置环境变量或启动 Docker Compose。可通过 `PLATFORM_CONFIG_PATH` 覆盖文件路径；相对路径始终以项目根目录为基准，与终端和 IDE 工作目录无关。显式设置为空路径视为配置错误。

部署项目维护 `middleware-service-deploy/platform.yaml.example`，示例结构如下：

```yaml
platforms:
  - code: registry
    name: 注册中心
    description: 消费入库请求并返回审核结果。
    secret: "change-this-registry-key"
    supports_a2a: false
    is_sub_management_platform: false
    a2aresponsibility: ""
    is_management_entry: false
```

| 字段 | 约束 |
| --- | --- |
| `code` | 全局唯一，小写字母开头，后续为小写字母、数字或分隔段落的单个连字符。 |
| `name` | 非空字符串，用于平台中文展示名称。 |
| `description` | 非空字符串，支持 Markdown；使用 YAML 的 `|` 保留换行，后端原样返回、不渲染，不参与权限判断。 |
| `secret` | 直接填写非空字符串，不同平台不能共用密钥；不执行环境变量替换。 |
| `supports_a2a` | 可选严格布尔值，默认 false，声明是否参与 A2A 协议。 |
| `is_sub_management_platform` | 可选严格布尔值，默认 false，声明是否为子管理平台。 |
| `is_management_entry` | 可选严格布尔值，默认 false，声明是否作为管理平台入口，与子管理平台标记独立。 |
| `a2aresponsibility` | A2A 协作职责字符串；is_sub_management_platform=true 时必填、非空且不能全为空白。其他平台可省略，默认空字符串；显式 null、非字符串不合法。 |
| `a2a_max_inflight` | 可选严格整数 1～100，本平台 A2A 消费窗口：子平台用于请求，顶层用于单聊反馈；省略或 null 使用默认窗口 3（可通过环境覆盖）。不能填写布尔值、字符串或浮点数。 |

code、name、description、secret 四个字段必填，supports_a2a、is_sub_management_platform 和 is_management_entry 可省略；拒绝未知字段、重复 YAML 字段和空平台列表。三个标记必须使用 YAML 布尔值 true/false，不能使用带引号字符串、数字或 null。名称、描述和密钥不能仅包含空白。密钥建议加引号，避免 YAML 将纯数字等内容识别为其他类型。示例密钥仅用于说明，部署时替换为各平台独立密钥。

子管理平台需声明具体职责，supports_a2a 即使为 false 也必须填写；不自动更改任何布尔标记。例如合同平台：

```yaml
is_sub_management_platform: true
supports_a2a: true
a2a_max_inflight: 5
a2aresponsibility: |
  负责解答合同相关问题。
  包括合同条款与履约问题。
```

职责在配置加载时校验，原样保留文字与换行，平台列表接口同步返回。该字段是职责元数据，不在本服务中自动触发 A2A 调度。

平台 A2A 领取时按 a2a_max_inflight 自动补齐剩余消费名额，客户端不传 limit；该窗口由同一平台所有用户和实例共享。子管理平台配置限制其请求执行数，knowledge-accumulation 配置限制其已领取但未确认的单聊反馈数，两类计数独立。广播聚合不占顶层单聊窗口。修改 YAML 后重启生效，未填写时继续使用默认窗口。a2a_max_inflight 不增加到平台列表公开字段，实际值在领取响应 capacity 中返回。

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
docker compose up -d --build --wait
```

Compose 的 `.env` 负责 RabbitMQ 凭据、业务流和后端运行参数。生产 Compose 中应用仓库位于 `./middleware-service`；本机开发使用 deploy 的独立 `docker-compose.dev.yml`，只运行 RabbitMQ 和 Redis，后端在 IDE 中启动，见[RabbitMQ 本地开发环境](../infrastructure/rabbitmq-stream.md)。

---

## 启动与运行边界

[配置加载器](../../../app/config/platform.py)使用 PyYAML 安全加载器和 Pydantic 校验。FastAPI lifespan 在接受请求前完成加载，文件缺失、格式错误、重复代号或密钥等问题都会阻止启动；错误信息不包含配置原文或密钥。

每个平台转为不可变 `Platform` 结构体，以代号索引存入只读的 `application.state.platforms`。后续服务可通过 `request.app.state.platforms[platform_code]` 访问。密钥使用 `SecretStr`，从模型序列化和对象表示中排除，需要时显式调用 `secret.get_secret_value()`。

平台列表返回所有配置平台，并原样返回 supports_a2a、is_sub_management_platform 与 is_management_entry 三个标记，以及 a2aresponsibility 职责说明。这些属性不改变平台登录、注册中心角色或已有消息流规则。[平台 A2A 接口](../../api/platform-a2a.md)使用 supports_a2a 与 is_sub_management_platform 校验目标和领取方角色，要求两者均为 true，且不允许顶层作为子平台目标。请求发布和领取已实现，配置标记本身不自动产生任务。is_management_entry 和职责说明不参与该角色校验。

原 show_in_list 已移除，旧配置必须删除该字段，否则按未知字段拒绝启动。升级时同步修改实际 YAML 与后端；三个标记未配置时均为 false，可根据实际平台能力设置，修改后重启生效。

配置修改后需重启应用，不热加载。本地实际配置已加入 `.gitignore`，Docker 构建也排除实际 `platform.yaml`。部署侧实际配置同样不提交仓库；文件由操作系统权限控制，容器运行用户须具备只读权限。

本模块不签发 token、不建立 RabbitMQ 连接；`registry` 仅为示例代号，实际注册中心身份由 RABBITMQ_REGISTRY_PLATFORM_CODE 配置。

---

## 验证

历史本地配置测试覆盖默认与覆盖路径、工作目录独立性、直接密钥、字段与重复项校验、敏感信息保护及启动生命周期；tests 目录按现有仓库规则不随代码分发。当前 a2a_max_inflight 的类型、范围、默认值和平台覆盖已在本机 `tests/test_platform_a2a.py` 中验证，见[平台 A2A 验证状态](platform-a2a.md#验证状态)。

```bash
conda activate middleware-service
python -m unittest discover -s tests -v
```
