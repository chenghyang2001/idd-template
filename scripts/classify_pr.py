#!/usr/bin/env python3
"""PR 意圖分類器（Haiku 4.5）。

讀取 PR 的標題、內文、diff，分類成 WRITER（新功能/重構）或 DEBUGGER（修 bug）。
分類結果寫入 GITHUB_OUTPUT 給後續 resolver job 取用。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic


def read_pr_metadata() -> tuple[str, str]:
    """從 GITHUB_EVENT_PATH 讀 PR 標題與內文。"""
    event_path = Path(os.environ["GITHUB_EVENT_PATH"])
    with event_path.open(encoding="utf-8") as f:
        event = json.load(f)
    pr = event.get("pull_request", {})
    title = pr.get("title", "") or ""
    body = pr.get("body", "") or ""
    return title, body


def fetch_pr_diff(pr_number: str) -> str:
    """用 gh CLI 抓 PR diff（GH_TOKEN 已透過 env 注入）。"""
    result = subprocess.run(
        ["gh", "pr", "diff", pr_number],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def classify(title: str, body: str, diff: str, model: str) -> str:
    """呼叫 Haiku 做單字分類，回傳 WRITER 或 DEBUGGER。"""
    client = anthropic.Anthropic()
    # diff 可能很長，截斷到 8000 字避免 token 爆掉（分類用 Haiku 不需要全 diff）
    diff_excerpt = diff[:8000]
    prompt = (
        "Classify this PR as either WRITER (adds feature, new spec, refactor) or "
        "DEBUGGER (fixes bug, failing test, regression). Respond with ONE word only.\n\n"
        f"PR Title: {title}\n\n"
        f"PR Body:\n{body}\n\n"
        f"PR Diff (truncated):\n{diff_excerpt}"
    )
    response = client.messages.create(
        model=model,
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip().upper()
    # 防呆：模型可能多吐字，只看是否包含關鍵字
    if "DEBUGGER" in raw:
        return "DEBUGGER"
    if "WRITER" in raw:
        return "WRITER"
    # 模型不合作時保守回 WRITER（新功能比修 bug 常見）
    print(f"Warning: unexpected classification '{raw}', defaulting to WRITER", file=sys.stderr)
    return "WRITER"


def write_output(mode: str) -> None:
    """寫入 GITHUB_OUTPUT 讓下游 job 透過 needs.classify.outputs.mode 取用。"""
    output_path = Path(os.environ["GITHUB_OUTPUT"])
    with output_path.open("a", encoding="utf-8") as f:
        f.write(f"mode={mode}\n")


def main() -> None:
    pr_number = os.environ["PR_NUMBER"]
    model = os.environ["CLASSIFY_MODEL"]
    print(f"Classifying PR #{pr_number} with model {model}")

    title, body = read_pr_metadata()
    print(f"PR title: {title}")

    # ROLLBACK 快速路徑：標題以 [ROLLBACK] 開頭時不必呼叫 API
    if title.upper().startswith("[ROLLBACK]"):
        print("Title starts with [ROLLBACK] — fast-path classification, skipping API call")
        write_output("ROLLBACK")
        return

    diff = fetch_pr_diff(pr_number)
    print(f"PR diff length: {len(diff)} chars")

    mode = classify(title, body, diff, model)
    print(f"Classification result: {mode}")

    write_output(mode)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"classify_pr.py failed: {e}", file=sys.stderr)
        sys.exit(1)
