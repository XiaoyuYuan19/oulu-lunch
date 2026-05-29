# Oulu 午饭

手机 PWA：Oulu 学生餐厅每日菜单（芬中对照 + 营业时间 + 价格）。

完整文档：[PROJECT.md](./PROJECT.md)

## 30 秒上手

1. 创建一个 GitHub repo（空的就行）
2. 把这个目录推上去
3. GitHub repo 配三件事：
   - Pages：从 `main` 分支的 `/public` 目录发布
   - Secret：`ANTHROPIC_API_KEY`
   - Actions workflow permissions：Read and write
4. Actions → "Update menus" → Run workflow
5. 浏览器打开 Pages 给你的 URL → 加到主屏

## 改餐厅

编辑 `scraper/restaurants.yaml`，把 lounaat.info 上的 URL 贴进去。
