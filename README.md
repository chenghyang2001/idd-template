# idd-template

> IDD（Issue-Driven Development）AI PR Pipeline 泛用模板

建立新專案後，在 GitHub Issue 輸入需求，背後自動完成：建立 feature branch → 建立 PR → Claude AI（classify → resolver → qa）→ 自動合併 → 關閉 Issue。

---

## 快速開始（5 分鐘）

### Step 1：使用此模板建立新 Repo

```bash
gh repo create <你的 repo 名稱> --template chenghyang2001/idd-template --public
cd <你的 repo 名稱>
```

### Step 2：修改 pipeline.config.json

唯一需要客製的檔案：

```json
{
  "spec_file": "spec.md",
  "implementation_target": "main.py",
  "test_target": "test_main.py",
  "language": "python",
  "run_command": ["python", "main.py"]
}
```

| 欄位 | 說明 |
|------|------|
| `spec_file` | 功能規格文件（Resolver 讀取後據此實作） |
| `implementation_target` | Resolver 修改的主要實作檔 |
| `test_target` | QA 寫入測試的檔案名稱 |
| `language` | `html` / `python` / `javascript` |
| `run_command` | QA 用 subprocess 執行此命令驗證 |

### Step 3：設定 GitHub Secrets

| Secret | 說明 |
|--------|------|
| `ANTHROPIC_API_KEY` | Anthropic API Key（需有 Credits）|
| `GH_PAT` | GitHub Personal Access Token（需 `repo` + `workflow` 權限）|

路徑：Repo → Settings → Secrets and variables → Actions

### Step 4：建立 spec.md 描述你的專案規格

範例：

```markdown
# 我的專案規格

## 目標
一個能輸入名字並回應 "Hello, {name}!" 的 Python 腳本。

## 實作規格
- `main.py` 接受命令列參數 `--name`
- 輸出格式：`Hello, {name}!`
```

### Step 5：建立 GitHub Issue 描述需求

Issue 建立後，pipeline 自動啟動：

```
Issue 建立
   ↓
issue-driven-pipeline.yml 建立 feature/issue-N branch → 開 PR
   ↓
pr-agent-pipeline.yml 啟動
  classify  → Claude Haiku   判斷 PR 類型（WRITER/DEBUGGER）
  resolver  → Claude Sonnet  依 spec.md 修改 implementation_target
  qa        → Claude Sonnet  產出測試 + 執行 pytest（最多 3 次重試）
  merge     → 自動合併並刪除 branch + 關閉 Issue
```

---

## 語言支援

| language | 測試策略 | 適用 |
|----------|---------|------|
| `html` | pathlib 讀 HTML 做結構斷言（不執行） | 前端單頁應用 |
| `python` | subprocess 執行 + assert stdout | Python 腳本 |
| `javascript` | subprocess + node + assert stdout | Node.js 腳本 |

---

## Pipeline 防護機制

| 機制 | 說明 |
|------|------|
| Bot-loop 防護 | 最後 committer 是 `github-actions[bot]` → 跳過整條 pipeline |
| Race condition 防護 | `concurrency: cancel-in-progress: true` |
| Fork 安全 | 只處理同 repo 的 PR |
| 自動合併範圍 | 只對 `comment-*` 和 `feature/*` branch 合併 |

---

## 專案結構

```
idd-template/
├── .github/workflows/
│   ├── issue-driven-pipeline.yml    # Issue → branch → PR
│   ├── pr-agent-pipeline.yml        # classify → resolver → qa → merge
│   └── auto-merge-comment-pr.yml    # 手動觸發備用
├── scripts/
│   ├── classify_pr.py               # Claude Haiku：分類 PR 意圖
│   ├── resolver_agent.py            # Claude Sonnet：修改實作檔
│   └── qa_agent.py                  # Claude Sonnet：產出測試並執行
├── pipeline.config.json             # ← 唯一需要客製的檔案
└── requirements.txt                 # anthropic + pytest
```
