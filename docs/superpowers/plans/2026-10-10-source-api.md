# TVBox 筛选与 Kazumi 接入实施计划

**Goal:** 自动筛选上游 TVBox 配置，发布 Kazumi 可导入的 HTTP JSON 配置，并在 Kazumi 提供一键获取入口。

**Architecture:** 此仓库负责采集、递归展开多仓、校验配置和 HTTP 来源、生成静态 JSON、Actions 定时运行及 Pages 发布。Kazumi 复用现有订阅及持久化能力，首次导入，后续保持同一订阅并保留用户单独修改和删除。

**Tech Stack:** Python 3.12、unittest、GitHub Actions / Pages；Kazumi Flutter / Dart。

## 接口约定

- `kazumi.json`: 标准 `{"sites":[...]}`，包含检测成功且兼容的 type 0/1/2/4 来源。URL 必须绝对化，key 稳定且唯一；不得执行 type 3、脚本或动态解密。
- `warehouse.json`: `{"urls":[{"name":"…","url":"…"}]}`，只列出实际可读取解析的单仓配置。
- `report.json`: 检测时间、上游来源、成功/失败及不兼容原因；区分配置可访问与 Kazumi 兼容。
- Pages 预期 URL: `https://amazingz-sys.github.io/tvbox-source-api/kazumi.json`，是否上线以实际部署为准。

## tvbox-source-api

- [x] 先补自动化测试：分类过滤、JSON 注释/BOM/尾逗号、多仓循环和相对链接、无效内容、兼容协议、去重、失败不覆盖旧输出。
- [x] 实现采集模块与 CLI，限制并发、超时、大小、递归深度和访问数量；只访问公共 HTTP(S)，校验重定向；保留来源检测报告。
- [x] 支持工作流手动触发、默认分支 push、每日定时，运行单测及实际筛选、上传报告，再发布 Pages。无兼容结果不得替换已部署站点。
- [x] 写清 README：本地运行、Pages 设置、Actions 手动/定时触发、接口 URL/结构、失败排查、兼容范围和上游署名。
- [x] 本地真实采集并检查生成结果；生成数据不手工冒充测试结果。

## Kazumi

- [x] 为一键导入添加测试：请求配置 URL、导入持久化、重复点击不重复订阅、刷新保留本地更改、失败不覆盖现有结果、忙碌状态。
- [x] 在 `lib/plugins/tvbox_config_controller.dart` 实现串行 import-or-refresh 入口，默认 URL 放入独立配置常量文件，支持 `--dart-define=TVBOX_SOURCE_API_URL=...`。
- [x] 在 `lib/pages/plugin_editor/tvbox_config_page.dart` 增加一键按钮及成功/失败/加载反馈。
- [x] 更新 `docs/tvbox-sources.md` 接入步骤。
- [x] 运行 TVBox 单测、相关 Flutter 分析，并通过真实生成的 `kazumi.json` 验证 Dart 解析器。

## 交付边界

用户要求在两个指定目录完成实现。保留现有未提交改动，不自动提交混杂的 Kazumi 改动。远端 push/部署状态必须明确记录，不能把预期 Pages URL 说成已上线。检测只证明当前节点配置与 API 协议响应可用，不代表每部影片可播放。

## Kazumi 验证记录

- 2026-10-10：TVBox 定向 33 项测试通过；全量 Flutter 258 项通过、14 项跳过。
- 定向分析无 error/warning，保留既有 `library_private_types_in_public_api` info。
- 真实 `public/kazumi.json` 的 14 个来源全部通过 Dart 解析器，无 warning。
- 默认 Pages URL 当前 HTTP 404，未发布；本地修改未 push。

## tvbox-source-api 验证记录

- 2026-10-10：`python3 -m unittest discover -s tests -v`，19 项确定性离线测试通过；覆盖真实发现的 JSONC 前置注释、重复来源顺序稳定性、DNS 地址回退、公网重定向与失败不覆盖旧输出。
- `python3 -m py_compile tvbox_source_api.py` 与 `git diff --check` 通过；两个工作流 YAML 解析通过，并核对了 UTC 01:23 调度、Pages 最小权限、依赖构建成功的部署及 always 报告上传。
- 最终真实运行：`python3 tvbox_source_api.py --resources-file /tmp/tvbox-upstream-resources.json --output-dir public`，检测开始于 2026-10-10T07:08:22.479444+00:00。15 个上游条目、45 个配置抓取、5 个有效单仓、28 个候选、14 个兼容来源、14 个探测失败、633 个跳过；产物含署名文件。
- 本机直连 GitHub raw 超时（12 秒和 30 秒均失败），上游目录使用当日已下载快照；所有配置和 API 仍执行真实网络请求，报告标明 `local_file`，诊断保存在被忽略的 `public-direct-check/report.json`。没有把该结果冒充 GitHub runner 结果。
- 未提交、未推送、未部署。README 已说明启用 Pages / Actions 的操作步骤及播放验证边界。
