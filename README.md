# 校历与课程日历

把中国科学技术大学本科教学日历（校历）和课程表合并成一个可订阅的 ICS 日历，发布到 GitHub Pages 后即可在手机/电脑的日历 App 中订阅。

## 文件结构

```
校历/basic.ics            校历原始文件（Google 日历导出，来源可替换）
2026秋季课程/2026.md      课程表（制表符分隔，见下方格式说明）
build_calendar.py         生成脚本：合并校历 + 课程，输出 calendar.ics 和 index.html
calendar.ics              生成结果（校历 2025-09-01 之后 + 课程）
index.html                订阅说明页（Pages 首页）
.github/workflows/build.yml  GitHub Actions：推送后自动重新生成并部署
```

## 发布到 GitHub Pages

1. 在 GitHub 上新建一个仓库（例如 `Calendar`），不要勾选自动生成 README。
2. 在本地推送：

   ```bash
   git remote add origin https://github.com/<你的用户名>/Calendar.git
   git push -u origin main
   ```

3. 开启 GitHub Pages（两种方式任选其一）：

   - **推荐（自动重建）**：仓库 Settings → Pages → Source 选择 **GitHub Actions**。之后每次 push 都会自动运行脚本重新生成并部署。
   - **简单方式（静态文件）**：Settings → Pages → Source 选择 **Deploy from a branch**，Branch 选 `main`、目录选 `/ (root)`。已提交的 `calendar.ics` 直接生效，更新需本地运行脚本后再推送。

4. 订阅地址为：

   ```
   https://<你的用户名>.github.io/Calendar/calendar.ics
   ```

   推送后重新运行一次 `python build_calendar.py`，`index.html` 会自动把订阅地址替换成真实地址。

## 更新课程

编辑 `2026秋季课程/2026.md`，每行一门课，制表符分隔，列含义：

```
课堂号  课程名称  起止周  教师  上课时间地点
ASTR6413P.01  致密星物理  2~18;18  戴子高  2408: 4(3,4,5);2408: 5(11,12,13)
```

- 起止周：支持 `2~18`、`2-18`、单个周 `18`，多个段用 `;` 或 `,` 分隔（重复周会自动去重）。
- 上课时间地点：格式为 `教室: 星期(节次)`，多段用 `;` 分隔；星期 `1` 为周一，`7` 为周日；节次支持 `3,4,5` 或 `3-5`。
- 节次时间按中国科学技术大学官方上课时间表换算（第 1-2 节 07:50-09:25，第 3-5 节 09:45-12:10，第 6-10 节 14:00-18:20，第 11-13 节 19:30-21:55）。

改完后本地运行：

```bash
python build_calendar.py
```

然后提交推送即可，Actions 会自动重建并部署。

## 每周固定事件（如组会）

在 `build_calendar.py` 顶部的 `RECURRING_EVENTS` 配置中添加，按秋季学期教学周自动展开，法定假日自动跳过：

```python
RECURRING_EVENTS = [
    {
        "summary": "组会",
        "weekday": 1,      # 1=周一 ... 7=周日
        "start": "19:00",
        "end": "21:00",
        "weeks": "1~20",   # 秋季学期教学周
    },
]
```

## 更新校历

把新的校历导出文件（ICS 格式）覆盖到 `校历/basic.ics`，重新运行脚本。生成时只保留 **2025-09-01 及之后** 的事件，更早的校历事件会被丢弃。

## 其他说明

- 课程事件按校历中的“秋季学期第一周”逐周展开；若某次课落在校历的法定假日（`休（...）` 事件）范围内，会自动跳过。
- 校历中的“补课”事件（如补周二课程）不会自动重排课程，如需调课请直接在课程表中手动调整。
- 生成脚本只依赖 Python 标准库，无需安装第三方包。
