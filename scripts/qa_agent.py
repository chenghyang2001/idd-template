#!/usr/bin/env python3
"""QA Agent（Sonnet 4.6）。

讀 resolver 改完的實作檔，請 Sonnet 寫出恰好 2 個 pytest 測試（happy + edge），
跑 pytest 收集結果，把結果以摺疊區塊留言到 PR。
pytest exit code 傳遞給呼叫者；上層 workflow 的 retry loop 依此判斷是否重試（最多 3 次）。
設定由 pipeline.config.json 決定，讓本流程能跨 repo 重用。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import anthropic


REPO_ROOT = Path(__file__).resolve().parent.parent


def load_pipeline_config() -> dict:
    """從 repo root 讀 pipeline.config.json，缺檔時回退到合理預設值。

    回傳字典含五個 key：spec_file / implementation_target / test_target /
    language / run_command。設計目的是讓本流程能跨 repo 重用。
    """
    config_path = REPO_ROOT / "pipeline.config.json"
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    # Fallback：新專案若未建立 config，使用 helloWeb 相容預設值
    return {
        "spec_file": "spec.md",
        "implementation_target": "index.html",
        "test_target": "test_index.py",
        "language": "html",
        "run_command": ["python", "-c", "print('index.html')"],
    }


CONFIG = load_pipeline_config()
SCRIPT_PATH = REPO_ROOT / CONFIG["implementation_target"]
TEST_PATH = REPO_ROOT / CONFIG["test_target"]


def read_target_script() -> str:
    """讀 resolver 改完後的實作檔（QA job checkout 已是最新 PR head）。"""
    if not SCRIPT_PATH.exists():
        raise FileNotFoundError(
            f"{CONFIG['implementation_target']} not found at {SCRIPT_PATH}"
        )
    return SCRIPT_PATH.read_text(encoding="utf-8")


def call_qa(code: str, model: str) -> str:
    """請 Sonnet 產出測試檔內容（純 Python，不要 markdown）。

    Prompt 中的 language / test_target / run_command 由 pipeline.config.json 決定，
    讓本流程能套用到不同 repo（不一定是 python + githubHello.py）。
    依 language 決定測試策略：
    - html：用 pathlib 讀 HTML 文字內容做 assert（不跑腳本）
    - 其他：用 subprocess 跑腳本並 assert stdout
    """
    client = anthropic.Anthropic()
    language = CONFIG["language"]
    test_target = CONFIG["test_target"]
    impl_target = CONFIG["implementation_target"]

    if language == "html":
        # HTML 沒有可執行腳本，改用 pathlib 讀檔做結構性斷言。
        # 明確要求 assert HTML 結構（標籤存在性、必要屬性）而非硬編碼內容，
        # 避免 resolver 修改 HTML 後測試因「舊內容消失」而誤報假陽性。
        test_strategy = (
            f"Use `pathlib.Path(__file__).resolve().parent / '{impl_target}'` "
            "to read the HTML file as text. "
            "Assert on HTML STRUCTURE (e.g., presence of tags like <html>, <head>, <body>, "
            "specific id/class attributes, required DOM elements from the spec), "
            "NOT on hardcoded text content that may change when the file is edited. "
            "Import `pathlib` and `re` as needed."
        )
    else:
        # python 及其他腳本語言：用 subprocess 跑腳本、assert stdout
        # 先判斷第一個元素是否為 Python 解譯器別名，決定是否替換為 sys.executable。
        # 若是 node / go run 等非 Python 命令，直接使用 config 中的原始命令，
        # 避免生成錯誤的 [sys.executable, 'server.js'] 測試碼。
        run_cmd = CONFIG["run_command"]
        python_aliases = {"python", "python3", "py"}
        if run_cmd and run_cmd[0].lower() in python_aliases:
            rest = run_cmd[1:]
            if rest:
                run_cmd_repr = "[sys.executable, " + ", ".join(repr(a) for a in rest) + "]"
            else:
                # ["python"] 無額外參數時，fallback 用 implementation_target 作為腳本路徑
                run_cmd_repr = f"[sys.executable, {repr(impl_target)}]"
        else:
            # 非 Python 語言（node、go run 等），直接用 config 中的命令
            run_cmd_repr = "[" + ", ".join(repr(a) for a in run_cmd) + "]"
        test_strategy = (
            f"Use `subprocess.run({run_cmd_repr}, capture_output=True, text=True, cwd=repo_root)` "
            "and assert on stdout."
        )
    prompt = (
        f"Read this {language} file and write a `{test_target}` containing EXACTLY 2 "
        "pytest test cases: (1) a normal/happy-path case named test_normal_*, "
        "(2) an edge case named test_edge_*. "
        f"{test_strategy} "
        "Return ONLY the raw Python code, no markdown fences, no commentary.\n\n"
        f"=== {impl_target} ===\n{code}\n"
    )
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def strip_markdown_fences(text: str) -> str:
    """Extract Python code from possibly-fenced model output.

    Handles three cases:
    1. Plain code (no fences) — returned as-is
    2. Code wrapped in ```python ... ``` (any position in text)
    3. Code wrapped in ``` ... ``` (no language tag)

    If fences exist, returns content of the FIRST code block found.
    Falls back to original text if regex finds no block.
    """
    block = re.search(r"```(?:python|py)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if block:
        return block.group(1).strip() + "\n"
    return text.strip() + "\n"


def write_test_file(content: str) -> None:
    """寫入測試檔，確保結尾單一 newline。"""
    if not content.endswith("\n"):
        content += "\n"
    content = content.rstrip("\n") + "\n"
    TEST_PATH.write_text(content, encoding="utf-8")


def run_pytest() -> tuple[int, str]:
    """跑 pytest，回傳 (exit_code, combined_output)。stderr 合併進 stdout 方便讀。"""
    test_rel = str(TEST_PATH.relative_to(REPO_ROOT)).replace("\\", "/")
    result = subprocess.run(
        ["pytest", test_rel, "-v", "--tb=short"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    combined = (result.stdout or "") + (result.stderr or "")
    return result.returncode, combined


def parse_pass_fail(output: str, exit_code: int) -> tuple[int, int]:
    """從 pytest summary 解析過/失，總共固定 2 個。"""
    passed = 0
    failed = 0
    m_passed = re.search(r"(\d+)\s+passed", output)
    m_failed = re.search(r"(\d+)\s+failed", output)
    if m_passed:
        passed = int(m_passed.group(1))
    if m_failed:
        failed = int(m_failed.group(1))
    # 萬一 summary 解析不到（pytest crash），用 exit code 兜底
    if passed == 0 and failed == 0:
        if exit_code == 0:
            passed = 2
        else:
            failed = 2
    return passed, failed


def build_comment(passed: int, failed: int, output: str) -> str:
    """組 PR 留言內文（含摺疊區塊放 pytest 輸出）。"""
    excerpt = output if len(output) <= 30000 else output[:30000] + "\n... (truncated)"
    return (
        "**QA Agent Result**\n\n"
        f"Generated 2 test cases. Result: PASS {passed}/2 | FAIL {failed}/2\n\n"
        "<details><summary>Test output</summary>\n\n"
        "```\n"
        f"{excerpt}\n"
        "```\n"
        "</details>\n"
    )


def post_pr_comment(pr_number: str, body: str) -> None:
    """用 gh CLI 留言（透過 --body-file 避免多行 shell escape）。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(body)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            ["gh", "pr", "comment", pr_number, "--body-file", tmp_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            print(f"PR comment failed: {proc.stderr}", file=sys.stderr)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def main() -> int:
    pr_number = os.environ["PR_NUMBER"]
    model = os.environ["QA_MODEL"]
    print(f"QA agent running on PR #{pr_number} (model={model})")

    mode = os.environ.get("MODE", "")
    if mode == "ROLLBACK":
        print("ROLLBACK mode detected — skipping AI-generated tests, doing structural check")
        impl_target = CONFIG["implementation_target"]
        # 確認實作檔存在且非空（語言無關的結構驗證）
        if not SCRIPT_PATH.exists():
            post_pr_comment(
                pr_number,
                f"❌ **QA ROLLBACK 失敗**：`{impl_target}` 不存在，回退可能有問題。"
            )
            return 1
        content = SCRIPT_PATH.read_text(encoding="utf-8").strip()
        if not content:
            post_pr_comment(
                pr_number,
                f"❌ **QA ROLLBACK 失敗**：`{impl_target}` 存在但檔案為空。"
            )
            return 1
        post_pr_comment(
            pr_number,
            f"**QA Agent Result (ROLLBACK)**\n\n"
            f"✅ 結構檢查通過：`{impl_target}` 存在且包含內容。\n\n"
            f"> 此次 QA 為結構驗證（非 AI 生成測試），適用於 ROLLBACK 場景。",
        )
        print("ROLLBACK QA: structural check passed")
        return 0

    code = read_target_script()
    print(f"Loaded {CONFIG['implementation_target']} ({len(code)} chars)")

    if not CONFIG["test_target"]:
        comment = (
            f"**QA Agent Result**\n\n"
            f"No `test_target` configured — structural check only.\n\n"
            f"✅ `{CONFIG['implementation_target']}` found ({len(code)} chars)\n"
        )
        post_pr_comment(pr_number, comment)
        print("No test_target — structural check passed")
        return 0

    raw = call_qa(code, model)
    test_code = strip_markdown_fences(raw)
    write_test_file(test_code)
    print(f"Wrote {TEST_PATH.name} ({len(test_code)} chars)")

    exit_code, output = run_pytest()
    print(f"pytest exit code: {exit_code}")
    print("--- pytest output ---")
    print(output)

    passed, failed = parse_pass_fail(output, exit_code)
    comment = build_comment(passed, failed, output)
    post_pr_comment(pr_number, comment)
    print(f"Posted QA comment: {passed} passed, {failed} failed")
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"qa_agent.py failed: {e}", file=sys.stderr)
        sys.exit(1)
