# Oulu 午饭 PWA — 项目文档

## 目标

PhD 在 Oulu 每天吃饭不便，看不懂芬兰菜单。手机上点开就能看到：**菜单原文 + 中文 + 图（V1 用 emoji 分类图标）+ 营业时间 + 价格**。

## 已确认决策

| 项目 | 选择 | 备注 |
|---|---|---|
| 范围 | Linnanmaa 校园几家学生餐厅 | V1 占位 4 家，可改 `scraper/restaurants.yaml` |
| 数据更新 | GitHub Actions 每天自动抓+翻译 | 工作日 07:30 UTC |
| 图片 | V1 emoji 分类图标，V2 留接口接真图 | data.json 加 `image_url` 字段前端自动切换 |
| 翻译 | Gemini 2.5 Flash（免费档） | 一次批量翻译，每日 200+ 请求免费额度绰绰有余 |
| 抓取 | lounaat.info HTML 解析 | 找今天菜单块 → 解析菜名 + 饮食标签 |
| 前端 | 单 HTML + Tailwind CDN + PWA | 加到主屏=APP，可离线读上次菜单 |
| 部署 | GitHub Pages | 0 服务器 0 月费 |

## 架构

```
浏览器/PWA ──▶ GitHub Pages (静态)
                  ▲
                  │ 提交 public/data.json
                  │
            GitHub Actions (每日 cron)
                  │
                  ├── scraper/scrape.py
                  │     ├── lounaat.info  (HTML 解析)
                  │     └── Claude Haiku  (翻译批量)
                  └── 写回 public/data.json
```

## 目录结构

```
oulu-lunch/
├── public/                       # GH Pages 根目录
│   ├── index.html                # PWA 主页（vanilla JS + Tailwind CDN）
│   ├── manifest.webmanifest      # PWA 清单
│   ├── sw.js                     # Service Worker（data.json network-first，外壳 cache-first）
│   ├── data.json                 # 当天菜单数据（Action 写入）
│   └── icons/icon.svg            # 应用图标
├── scraper/
│   ├── scrape.py                 # 抓取 + 翻译入口
│   ├── restaurants.yaml          # 餐厅配置（URL/营业时间/价格）
│   └── requirements.txt
├── .github/workflows/
│   └── update.yml                # 每日 cron + 手动触发
├── .gitignore
├── PROJECT.md                    # 本文档
└── README.md                     # 一次性 setup 步骤
```

## data.json 结构

```json
{
  "updated_at": "2026-05-29T07:30:00+00:00",
  "restaurants": [
    {
      "name": "Foobar",
      "hours": "10:30-14:30",
      "price": "2.95",
      "location": "Linnanmaa",
      "url": "https://www.lounaat.info/lounas/foobar/oulu",
      "dishes": [
        {
          "fi": "Lohikeitto",
          "zh": "三文鱼汤",
          "emoji": "🐟",
          "tags": ["无乳糖"],
          "image_url": null   // V2 接真图时填这里
        }
      ]
    }
  ]
}
```

## 一次性 Setup

1. 我帮你 `git init` + 初始 commit
2. 你去 GitHub 创个新 repo（public 或 private 都行，public 用 Pages 免费额度更宽）
3. 我把本地 push 上去
4. GitHub 上：
   - **Settings → Pages**：Source = `Deploy from a branch`，Branch = `main`，Folder = `/public` → Save
   - **Settings → Secrets and variables → Actions → New repository secret**：`GEMINI_API_KEY` = 你的 key（在 https://aistudio.google.com/app/apikey 免费创建）
   - **Settings → Actions → General → Workflow permissions**：勾 "Read and write permissions"
5. **Actions** 页签 → Update menus → Run workflow（手动触发一次，产出第一份 data.json）
6. 等 1-2 分钟 Pages 部署完，浏览器开 `https://<your-gh-username>.github.io/<repo>/`
7. iOS Safari → 分享 → "添加到主屏幕" / Android Chrome → 菜单 → "安装应用"

## 改餐厅

编辑 `scraper/restaurants.yaml`：
- 去 lounaat.info 搜餐厅名
- 复制页面 URL 贴到 `url`
- 填 `hours` / `price` / `location`
- 推到 GitHub → Action 下次跑会用新列表

## 本地调试

```bash
# 跑一次 scraper（需要 ANTHROPIC_API_KEY）
pip install -r scraper/requirements.txt
GEMINI_API_KEY=AIzaSy... python scraper/scrape.py

# 本地预览
cd public && python -m http.server 8000
# 开 http://localhost:8000
```

## V2 升级路径（不在 V1 范围）

- **真图**：scraper 里加一步：每个菜名 → Pexels API 搜 → 取第一张 → 写入 `image_url`
- **更多餐厅**：市中心 Kastari、Antell 等加进 yaml
- **拍照模式**：去餐厅吃 à la carte 时拍菜单 → 浏览器调相机 → 直接送 Claude Vision → 返回结构化菜单
- **过敏过滤**：data.json 已带 tags，前端加复选过滤"避开 X"
- **周菜单缓存**：每周一抓一次全周，路径切换显示
- **多语言**：英语界面切换（zh ↔ en）

## 已知风险 / 待验证

- ❗ **lounaat.info HTML 结构**：scrape 用了多重 selector 兜底，但实际站点改版可能解析不到。第一次跑后看 data.json 验证。
- ❗ **restaurants.yaml 里 4 个 slug 是占位**：你打开各 URL 确认能打开真的菜单页；不行就换。
- ❗ **菜名变化**：每日重新翻译，意味着每天 ~20-40 道菜送 Gemini。免费档完全够用；以后想省调用可加翻译缓存（同 fi 名复用之前 zh）。
- ❗ **饮食标签解析**：lounaat 用 `(L, G, VL)` 等代码，我做了映射，但站点偶尔会用别的格式。
