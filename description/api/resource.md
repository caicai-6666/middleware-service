# 审核附件读取接口

registry 根据申请消息中的文件引用读取入库或删除审核附件，返回原始二进制。路由位于 `app/router/resource.py`，文件访问由 `app/service/resource.py` 提供，在 `app/main.py` 装配为 `app.state.resources`。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [读取入库审核附件](#读取入库审核附件) | `GET` | `/middleware-service/api/resource/ingestion-requests/{file_id}` |
| [读取删除审核附件](#读取删除审核附件) | `GET` | `/middleware-service/api/resource/deletion-requests/{file_id}` |

## 通用约定

仅配置 `RABBITMQ_REGISTRY_PLATFORM_CODE` 指定的注册中心有效会话可读，默认 `registry`；身份通过 `Depends(require_platform)` 从 token 缓存取得，不接受客户端传入平台代号。角色配置复用已有配置，无新增环境变量。

文件必须在对应审核附件目录中存在，下载不自动 pull、ack、续期或删除文件。文件引用可来自申请 pull 返回的 `message.file`；请在 ack 清理之前下载。

读取时打开文件，以 1 MiB 分块在线程池中读取和传输，不将整个文件加载到内存。正常结束、客户端断开或读取错误都会关闭文件句柄。下载已打开文件后，ack 删除路径不会中断现有下载；删除后新发起的下载返回 404。

文件内容、后缀和人员信息均来自上传申请，下载不验证实际文件格式。返回 `application/octet-stream`，由调用方结合消息中的后缀处理。

---

## 读取入库审核附件

`GET /middleware-service/api/resource/ingestion-requests/{file_id}`，读取 `data/ingestion-request/file` 中的临时审核文件。

### 认证方式

Header：`Authorization: Bearer <registry access_token>`，必须为有效注册中心会话。

### 请求参数

无请求体。

| 位置 | 字段 | 类型 | 必填 | 约束与含义 |
| --- | --- | --- | --- | --- |
| path | `file_id` | string | 是 | 申请消息中的 `file.file_id`，1–128 位英文字母、数字、下划线或连字符；不是 source_id 或 passport。 |
| query | `file_extension` | string | 是 | 消息中的 `file.file_extension`，1–16 位英文字母或数字，不带点，读取时转为小写。 |

### 成功响应

`200 OK`，`application/octet-stream`，响应体是原始文件字节。

```text
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="<file_id>.<小写后缀>"
Content-Length: <实际文件字节数>
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

下载名称由服务器生成，不使用原始文件名或业务名称。

### 错误响应

- `401`：访问令牌缺失、无效或过期。
- `403`：调用平台不是注册中心。
- `404`：文件不存在、已清理、是目录/符号链接/其他非普通文件。
- `422`：文件标识或后缀缺失/格式不合法。
- `503`：打开文件失败，例如文件没有读取权限。

```json
{"detail":"审核附件不存在或已清理"}
```

### 请求示例

替换令牌、file_id 及后缀；响应保存到本地文件：

```bash
curl --fail "http://127.0.0.1:22000/middleware-service/api/resource/ingestion-requests/<替换为file_id>?file_extension=pdf" -H "Authorization: Bearer <registry access_token>" --output ./attachment.pdf
```

### 行为与边界

服务器只构建 `data/ingestion-request/file/<file_id>.<file_extension>`，不接受完整磁盘路径或路径分隔符，不跟随文件符号链接。接口不校验该文件是否为 registry 当前 pull 的待确认消息，允许读取目录内已存在的普通审核附件。

本接口没有修改消费 offset 的副作用，不删除共享流消息或文件。registry 应下载完成并处理申请后再 ack。

若传输开始后磁盘读取失败，连接可能中断，不能再改写为 JSON 错误响应；调用方应检查下载成功状态和文件完整性。首次认证成功后不会在每个分块重新认证。

---

## 读取删除审核附件

`GET /middleware-service/api/resource/deletion-requests/{file_id}`，读取 `data/deletion-request/file` 中的临时审核文件。

### 认证方式

Header：`Authorization: Bearer <registry access_token>`，必须为有效注册中心会话。

### 请求参数

无请求体。

| 位置 | 字段 | 类型 | 必填 | 约束与含义 |
| --- | --- | --- | --- | --- |
| path | `file_id` | string | 是 | 申请消息中的 `file.file_id`，1–128 位英文字母、数字、下划线或连字符；不是 source_id 或 passport。 |
| query | `file_extension` | string | 是 | 消息中的 `file.file_extension`，1–16 位英文字母或数字，不带点，读取时转为小写。 |

### 成功响应

`200 OK`，`application/octet-stream`，响应体是原始文件字节。

```text
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="<file_id>.<小写后缀>"
Content-Length: <实际文件字节数>
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

下载名称由服务器生成，不使用原始文件名或业务名称。

### 错误响应

- `401`：访问令牌缺失、无效或过期。
- `403`：调用平台不是注册中心。
- `404`：文件不存在、已清理、是目录/符号链接/其他非普通文件。
- `422`：文件标识或后缀缺失/格式不合法。
- `503`：打开文件失败，例如文件没有读取权限。

```json
{"detail":"审核附件不存在或已清理"}
```

### 请求示例

替换令牌、file_id 及后缀；响应保存到本地文件：

```bash
curl --fail "http://127.0.0.1:22000/middleware-service/api/resource/deletion-requests/<替换为file_id>?file_extension=pdf" -H "Authorization: Bearer <registry access_token>" --output ./attachment.pdf
```

### 行为与边界

服务器只构建 `data/deletion-request/file/<file_id>.<file_extension>`，不接受完整磁盘路径或路径分隔符，不跟随文件符号链接。接口不校验该文件是否为 registry 当前 pull 的待确认消息，允许读取目录内已存在的普通审核附件。

本接口没有修改消费 offset 的副作用，不删除共享流消息或文件。registry 应下载完成并处理申请后再 ack。

若传输开始后磁盘读取失败，连接可能中断，不能再改写为 JSON 错误响应；调用方应检查下载成功状态和文件完整性。首次认证成功后不会在每个分块重新认证。
