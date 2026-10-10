# tvbox-source-api

从 [laoma528/awesome-zhuiju-free](https://github.com/laoma528/awesome-zhuiju-free) 的 **TVBox 配置**分类发现地址，实际读取配置并探测其中的 HTTP 搜索接口，生成可被 Kazumi 导入的静态配置。GitHub Actions 每天自动检测，通过后部署到 GitHub Pages。运行只需 Python 3.12 标准库，没有 pip 依赖，也不需要服务器或访问令牌。

本仓库提供采集器和发布工作流；**写好工作流不代表接口已经上线**。须完成下面的 Pages 设置，并确认 `deploy` 成功后再使用公开 URL。

## 输出接口

以 `AmazingZ-sys/tvbox-source-api` 为例，成功部署后的预期地址：

| 地址 | 用途 |
| --- | --- |
| [kazumi.json](https://amazingz-sys.github.io/tvbox-source-api/kazumi.json) | Kazumi 导入入口，标准 `{"sites":[...]}`，只包含探测成功的兼容 HTTP 来源 |
| [warehouse.json](https://amazingz-sys.github.io/tvbox-source-api/warehouse.json) | TVBox / 影视仓多仓入口，标准 `{"urls":[{"name":"名称","url":"原始配置地址"}]}` |
| [report.json](https://amazingz-sys.github.io/tvbox-source-api/report.json) | 最近一次**成功发布**所对应的检测报告 |
| [ATTRIBUTION.md](https://amazingz-sys.github.io/tvbox-source-api/ATTRIBUTION.md) | 随产物发布的上游署名与修改说明 |

`warehouse.json` 保留实际读取、解析通过的**原始单仓 URL**，其中可能仍有 type 3、JAR 和脚本来源，是否支持由 TVBox / 影视仓客户端决定。它不把多个仓库的 Spider 配置拼成一个虚假的完整仓库。Kazumi 应使用 `kazumi.json`。

`kazumi.json` 结构示例（示意值，并非真实检测结果）：

```json
{
  "sites": [
    {
      "key": "tvbox-稳定标识",
      "name": "来源名称",
      "type": 1,
      "api": "https://example.com/api.php",
      "headers": {"User-Agent": "来源要求的请求头"},
      "ext": {"example": "保留原始参数"}
    }
  ]
}
```

## 首次启用自动发布

1. 将本仓库代码推送或合并到 GitHub 仓库的默认分支 `main`。定时与手动工作流需要存在于默认分支；仅在开发分支保留文件不会启动定时发布。
2. 打开仓库 **Settings → Pages → Build and deployment → Source**，选择 **GitHub Actions**。不选择 `Deploy from a branch`，不需要 `gh-pages` 分支。
3. 打开 **Actions**。若工作流被禁用（例如刚 fork 的仓库），先启用仓库工作流，再选择 **Collect and publish sources → Run workflow → main → Run workflow**。
4. 等待 `build` 和 `deploy` 都成功。`build` 会先运行离线单测，再实际抓取和探测；`deploy` 环境名为 `github-pages`。若仓库环境设置要求审批，需要按仓库现有规则审批部署。
5. 从本次工作流的 `github-pages` 部署链接确认站点，再打开上述三个 JSON URL。根路径没有首页，直接访问 JSON 文件即可。
6. 确认 `kazumi.json` 有非空 `sites`，并核对 `report.json` 的 `tested_at`、`status` 与 `counts.compatible_sites`，然后在 Kazumi 的 TVBox 配置页一键获取，或粘贴 `kazumi.json` URL 导入。

发布工作流在 `main` 的 push、手动触发，以及每天 **UTC 01:23 / 北京时间 09:23** 运行。GitHub 的定时调度可能延迟，不是精确时钟；长期不活跃仓库的计划任务也可能被 GitHub 禁用，需要到 Actions 重新启用。若默认分支不是 `main`，同时修改 `publish.yml` 的 push 分支和 `test.yml` 的排除分支。

工作流不写回仓库：构建只授予 `contents: read` 与读取 Pages 配置所需的 `pages: read`，部署独立授予 `pages: write` 与 `id-token: write`。不需要 PAT，也无需把仓库的默认工作流权限整体改成可写。设置方式参考 [GitHub 官方 Pages 自定义工作流说明](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。

Fork 或改名后，Pages URL 会变为 `https://<用户名>.github.io/<仓库名>/kazumi.json`。Kazumi 可直接导入新 URL；如果构建本次配套修改的 Kazumi，也可用 `--dart-define=TVBOX_SOURCE_API_URL=https://<用户名>.github.io/<仓库名>/kazumi.json` 修改一键获取入口。

## 本地运行与验证

```sh
python3 --version
python3 -m unittest discover -s tests -v
python3 tvbox_source_api.py --output-dir public
```

成功退出码为 `0`，输出写入 `public/`，该目录被 Git 忽略。每次命令结束会打印状态和数量；详细原因在 `public/report.json`。生成数据无需手动提交。

```sh
python3 -m json.tool public/kazumi.json
python3 -m json.tool public/report.json
python3 -m http.server 8000 --directory public
```

本地 HTTP 服务只用于查看产物。采集器出于访问边界要求不采集 localhost 或内网来源；离线单测使用注入的网络响应，不依赖服务或真实网络。

如果本机直连 `raw.githubusercontent.com` 超时，但系统代理能访问，可先自行下载上游目录，再让采集器直接请求目录中的公开配置/API：

```sh
curl --fail --location --max-time 60 \
  'https://raw.githubusercontent.com/laoma528/awesome-zhuiju-free/main/resources/resources.json' \
  --output resources.local.json
python3 tvbox_source_api.py --resources-file resources.local.json --output-dir public
```

`curl` 会按其代理设置运行；采集器**不读取代理环境变量**，实际配置/API 探测仍是直连。`--resources-file` 只是用本地目录快照替代上游目录 GET，不会跳过配置和搜索探测，报告中 `upstream.input` 会标记为 `local_file`。Actions 默认始终实时获取上游。上游在本机与 GitHub runner 的可访问情况可能不同，不能把本机结果当作云端发布成功的证明。

常用参数：

```sh
python3 tvbox_source_api.py \
  --probe-keyword '海贼王' \
  --timeout 12 \
  --workers 8 \
  --max-depth 4 \
  --max-configs 100 \
  --max-sites 300 \
  --max-bytes 4194304 \
  --output-dir public
```

| 参数 | 默认值及意义 |
| --- | --- |
| `--upstream-url` | 上游 `main/resources/resources.json` 的 raw 地址 |
| `--resources-file` | 可选的本地上游目录；支持相同 JSONC 解析 |
| `--probe-keyword` | `海贼王`，探测用搜索词 |
| `--timeout` | 12 秒，每个 HTTP 请求链的超时预算；操作系统 DNS 解析仍受系统解析器管理 |
| `--workers` | 8，同时抓取或探测的线程数 |
| `--max-depth` | 4，多仓嵌套深度，上游直接配置为第 0 层 |
| `--max-configs` | 100，最多发起的配置抓取数 |
| `--max-sites` | 300，最多进行搜索探测的去重兼容候选数 |
| `--max-bytes` | 4 MiB，单次响应或本地目录文件的读取上限 |

达到配置、候选或深度限制时 `report.truncated` 为 `true`，已验证结果仍可发布。CLI 可调整边界；Actions 另有 20 分钟构建上限。

## 到底验证了什么

1. **发现地址。** 读取上游 `resources/resources.json` 的 `resources` 数组，只选择 `category == "tvbox_config"` 的条目，并使用实际配置字段 `url`。`link_url` 可能是官网或展示链接，不作为配置地址。
2. **读取真实内容。** 对配置发送 GET，跟随最多 4 次重定向。支持 UTF-8 BOM、`//` / `/* ... */` 注释和尾逗号；字符串中的 URL 和注释符号不会被破坏。不执行 JavaScript、Python、JAR 或动态解密。HTML 页面、图片/二进制、加密文本和不合法结构会记录失败原因。
3. **展开多仓。** 递归支持 `urls` 的 `name/url` 和 `storeHouse` 的 `sourceName/sourceUrl`；相对 URL 以响应最终地址解析。规范化 URL 后去重、检测循环，并受数量和深度限制。
4. **选取 Kazumi 子集。** type 0 为 XML，type 1/2 为标准 JSON，type 4 必须是远程 HTTP(S) API。type 3、`csp_`、本地脚本或其它非 HTTP 协议不进入 Kazumi 输出；`searchable` 为 `false`、`0` 或字符串 `"0"` 的站点跳过。保留 `header/headers`、`ext`，把 `api` 与非空 `playUrl` 解析成绝对地址。
5. **探测搜索协议。** 请求与 Kazumi 当前 `TvBoxRuleStrategy` 对齐：GET 携带 `wd=<探测词>`、`quick=false`，type 0 另有 `ac=list`。保留已有查询参数，`ext` 字符串或紧凑 JSON 通过 `extend` 传递；超过 1000 个 UTF-16 单元时用 POST 表单，已有查询参数一并进入表单。HTTP 200 只是必要条件，响应还必须是标准 JSON `list` 中包含有效 `vod_id/vod_name`，或 XML 中有效 `video/id/name`，且至少有一条可用结果。
6. **稳定身份与去重。** 站点 key 基于“原始单仓 URL + 原始站点 key”生成哈希，因此改名、API 换域名或 CDN 重定向不会轻易变成新来源。缺失原始 key 时才退回协议/URL/参数指纹；相同协议/URL/请求头/参数的来源只探测一次，按原始配置 URL 与稳定 key 的字典序选择代表，避免上游列表重排导致身份漂移。上游更换站点 key、移除选中的重复来源代表或整体更换原始配置 URL 仍可能影响标识。

这里验证的是**当前检测节点的配置读取与指定关键词搜索**。不请求每部影片详情、不逐条探测播放链接、不验证 DRM、播放器兼容性、解析器和媒体分片，不能承诺每部作品都可播放。搜索返回空列表的站点会被保守排除，即使它可能支持别的关键词；可在本地换词复测。地区、DNS、UA 限制和时段变化都会影响结果。

所有抓取目标及每个重定向都必须解析为公网 HTTP(S)，拒绝 URL 用户名密码、环回、私网、链路本地、保留与多播地址；连接使用已验证的 IP，TLS 仍校验原始域名。响应体受大小限制，不访问/执行扩展参数引用的脚本。报告中的 URL 查询值已脱敏，不写请求头、`ext` 或原始错误堆栈；导入配置本身需保留公开上游要求的参数，因此不应把个人私密配置作为输入。

## 失败保护与报告排查

若上游不可读取、没有候选、所有搜索探测失败，命令退出 `1`，仍生成报告，并且**不覆盖已有 `kazumi.json` 和 `warehouse.json`**。Actions 的报告上传使用 `if: always()`；采集失败时 `build` 失败，Pages 上传与依赖它的 `deploy` 不执行，上一次成功部署继续服务。离线测试在采集前失败时还没有采集报告，应查看测试日志。

因此线上 `report.json` 显示的是最后成功部署对应的检测；**失败当天的新报告**要到 Actions 对应运行的 Artifacts 下载 `source-report-<run_id>-<run_attempt>`（保留 14 天）。首次运行就失败时没有旧部署，接口自然不会出现。不要用空配置覆盖此前成功结果来“修复”工作流。

报告主要字段：

- `tested_at`：本轮检测开始时间，ISO 8601 / UTC。
- `upstream`：目录来源、分类、许可和实时/本地输入方式。
- `counts`：发现数、实际抓取数、有效单仓数、候选数、成功数、失败及不支持数量。
- `configs`：配置读取、结构和多仓展开结果；`sites`：站点跳过/探测结果及稳定 key。
- `scope: config_and_search_only`、`probe_keyword`：验证边界与关键词。
- `limits` 与 `truncated`：运行边界和是否触及上限。

常见原因：

| reason | 含义与处理 |
| --- | --- |
| `timeout` / `dns_failed` / `network_failed` / `tls_failed` | 检测节点网络或证书问题；换时段复测，并对比本地/runner 网络 |
| `http_403` / `http_401` / `http_404` 等 | 服务拒绝、需要授权或地址失效；查看上游是否更新配置 |
| `html_or_xml_not_config` | 返回网页、拦截页或非 JSON 配置 |
| `binary_or_non_utf8` / `encrypted_or_unsupported_content` | 图片、其它编码或加密内容；本项目不执行解密脚本 |
| `invalid_jsonc` / `missing_sites_or_warehouse` | 不合法 JSONC 或不符合单仓/多仓结构 |
| `unsupported_type` / `http_only` / `search_disabled` | 不属于 Kazumi 支持的可搜索 HTTP 子集 |
| `invalid_search_protocol` / `empty_search_results` | API 返回非标准协议或指定关键词没有有效结果 |
| `non_public_target` / `credentials_in_url` | 地址超出公网抓取范围或含 URL 认证信息 |
| `body_too_large` / `redirect_limit` | 超过响应大小或重定向边界 |

Pages 返回 404 时，先确认工作流文件已经在默认分支、Pages Source 为 GitHub Actions，以及 `deploy` 是否真的成功。对 `configure-pages` 的未启用 Pages 错误，应完成 Settings 设置后重跑；不要放宽检测逻辑。PR 工作流只运行离线测试，既不采集也不部署。

## 本地实测记录

2026-10-10 07:08 UTC（北京时间 15:08）使用当日下载的上游目录快照，在本地真实读取配置并请求搜索 API：15 个分类条目，45 个配置请求，5 个有效单仓，28 个去重 HTTP 候选，14 个搜索协议验证成功，14 个搜索失败，633 个站点被跳过。配置失败 39 个，另有 1 个可展开多仓，因此有效单仓数与抓取数不直接相减。

这次默认关键词为 `海贼王`，输出已供配套 Kazumi 解析器做真实产物兼容测试。此数字只是该次检测快照，不能代表日后结果；完整生成文件保留在本地被忽略的 `public/`。该机器直连 GitHub raw 超时，所以这次报告标明 `upstream.input: local_file`。未在本文把预期 Pages 地址标记为已上线。

## 目录与署名

```text
tvbox_source_api.py          采集、探测、生成 CLI
tests/test_collector.py      无真实网络依赖的回归测试
.github/workflows/publish.yml  定时/手动采集和 Pages 发布
.github/workflows/test.yml     PR 与开发分支测试
ATTRIBUTION.md              上游署名与修改说明
public/                    本地生成输出（不提交）
```

资源目录来自 **laoma528 及 awesome-zhuiju-free 贡献者**，上游使用 [CC BY 4.0](https://github.com/laoma528/awesome-zhuiju-free/blob/main/LICENSE)。本项目只筛选 TVBox 分类、展开公开配置、探测和重新组织结果；完整署名、许可链接及修改说明见 [ATTRIBUTION.md](ATTRIBUTION.md)，并随 Pages 产物发布。第三方 API、影视内容及脚本的权利不因目录许可而改变。
