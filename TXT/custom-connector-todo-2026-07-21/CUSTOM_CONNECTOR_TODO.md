# 自定义商品接口后续对接待办

状态：待继续
创建时间：2026-07-21
优先级：用户提供新站点接口资料后继续

## 已完成

- 自定义商品连接器后端 V1 已实现。
- 支持样例 JSON 预览、真实只读请求、字段映射、分页、版本、测试、激活和商品同步。
- Token/密钥使用 `CONNECTOR_SECRET_KEY` 加密保存，接口响应不回显敏感值。
- 商品同步会保存 TDK、canonical、图片 ALT 等 SEO 审计结果。
- ExDivo 真实样例 104 条全部映射成功，映射错误为 0。
- 数据库迁移已应用；全量测试为 `270 passed`。

## 待办

- [x] 配置 `CONNECTOR_SECRET_KEY`（当前保存在 Windows 用户环境变量）；仍需通过 `start-backend.bat` 重启后端。
- [ ] 获取新站点接口资料：接口 URL、GET/POST、鉴权方式、请求参数、返回 JSON、分页规则、对应 `site_id`。
- [ ] 先调用 `POST /api/v1/connectors/preview` 验证样例映射，不访问外部接口、不写数据库。
- [ ] 再调用 `POST /api/v1/connectors/test-request` 验证真实接口和 Token，仅执行受限只读请求。
- [ ] 修正字段映射，确保 `external_id`、`title`、URL、价格、状态、TDK、图片和更新时间正确。
- [ ] 保存连接器，依次执行 `test → activate → sync-products`。
- [ ] 抽样核对同步后的产品数量、字段、分页、幂等性和 SEO 缺口。
- [ ] 制作接口文档中的可视化连接器配置向导，让用户能够自行粘贴接口文档、配置和测试连接器。
- [ ] 后续单独设计 AI SEO 修正流程：生成建议、展示差异、人工审核、平台写回权限和回滚记录。

## Avinoti 实测记录（2026-07-21）

- Bruno 集合位置：`C:/Users/PC/Desktop/Avinoti API/`；项目文档不保存其中的真实 Token。
- `GET https://openapi.oemapps.com/products/list?page=1&limit=100` 实测成功：`code=0`，共 26 个商品，单页返回 26 个。
- 当前 SEO 缺口：26 个缺 Meta Title、26 个缺 Meta Description、26 个缺 Meta Keywords、87 张商品图片缺 ALT。
- 商品列表 Bruno 请求中存在两个同名 `token` Header，应只保留与“商品列表（有总数）”相同的有效 Token。
- 原编辑 URL 使用 `/products/138660225`，其中 `138660225` 是 variant ID；更新接口路径要求商品 ID，本次目标商品应使用 `/products/13576711`。
- `编辑商品.yml` 实际 Body 是 `{}`，旁边的 `编辑商品 Body.yml` 不会自动作为请求体发送。
- 更新接口是完整 PUT，不是局部 PATCH：空 Body 依次触发商品不存在、title 必填、spec_mode 必填、图片集不能为空等错误。
- 使用 `GET /products/13576711` 的完整真实商品结构进行同值 PUT 后，接口返回 `code=0 success`；TDK、标题、价格、库存、图片等业务内容保持原值。
- 重要副作用：同值 PUT 仍更新商品 `updated_at`，并把 4 个 variant ID 从 `138660225–138660228` 重建为 `149238287–149238290`。因此 AI SEO 写回不能直接批量启用。

### Avinoti 后续接入前置条件

- [ ] 在站点管理中创建 Avinoti 站点记录并取得 `site_id`；当前本地数据库没有 Avinoti 站点。
- [ ] 配置 `CONNECTOR_SECRET_KEY` 后保存只读商品连接器 Token。
- [ ] 通过内置 OEMApps 预设只填写 `site_id + token`，完成 26 个商品的正式只读同步与 SEO 缺口页面。
- [ ] 确认平台重建 variant ID 是否会影响订单、购物车、库存、广告 Feed、站内链接或第三方集成。
- [ ] 为写回增加逐商品快照、字段差异、人工批准、同值保护、单商品执行、回读校验和失败停止。
- [ ] 正式更新时必须先 GET 商品详情，将 AI 批准的 TDK/ALT 合并进完整商品结构，再 PUT 商品 ID；禁止把示例 Body 或 variant ID 直接发送。

后端已完成上述写回保护：预览返回快照哈希，执行要求显式确认 variant 重建风险，且会保存前后快照、variant ID 和回读校验结果。尚未配置 Avinoti 站点和正式 Token，未启用批量写回。

## ExDivo 接入完成记录（2026-07-21）

- [x] ExDivo 主站已写入数据库。
- [x] OEMApps 连接器已使用站点专用 Token 完成测试和激活。
- [x] 104 个商品已同步，映射错误和拒绝数量均为 0。
- [x] 同步结果：49 个缺 Meta Title、49 个缺 Meta Description、461 张图片缺 ALT。
- [x] `CONNECTOR_SECRET_KEY` 已保存到 Windows 用户环境变量，Token 已加密入库。
- [ ] 通过 `start-backend.bat` 重启 8000 端口后端，加载本轮代码与用户环境变量。

## ExDivo 产品分类接入完成记录（2026-07-21）

- [x] 全部专辑读取接口真实验证：共 12 个专辑。
- [x] 专辑详情与完整 PUT 编辑接口真实验证。
- [x] 使用单成员专辑同值 PUT，内容和成员保持不变。
- [x] 12 个专辑已同步到本地分类表，成员数量通过 104 个商品库存反向校验。
- [x] 分类 SEO 审计：10 个缺 Meta Title，10 个缺 Meta Description。
- [x] 分类修改已增加预览、快照哈希、成员数量校验、显式 `is_top` 风险确认、审计和回读。

## 验收标准

- 获取数量与上游接口一致，分页没有遗漏或重复。
- 必填字段映射错误为 0。
- 重复同步不会产生重复商品。
- Token、Authorization、auth_keys 等敏感内容不会出现在 API 响应或运行日志中。
- SEO 审计能准确识别缺少的 title、meta title、meta description、canonical 和图片 ALT。
- AI 修正上线前不得自动修改上游商品页面；写回必须有独立权限和人工审核边界。

## 恢复入口

- 接口文档：`docs/API.md` 的“自定义商品数据连接器”。
- 路由：`app/api/v1/connectors.py`。
- 配置与映射：`app/connectors/custom_data.py`。
- 同步服务：`app/services/custom_connector_service.py`。
- 数据库迁移：`db/migrations/018_custom_data_connectors.sql`。
