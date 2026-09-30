"""依次运行 tests/ 下全部测试脚本并汇总结果。

用法：
    KIRA_FRAMEWORK_DIR=/path/to/KiraAI python3 tests/run_all.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    tests = sorted(HERE.glob("test_*.py"))
    if not tests:
        print("未找到测试文件")
        return 1
    failed = []
    for test in tests:
        print(f"\n{'#' * 64}\n# {test.name}\n{'#' * 64}", flush=True)
        rc = subprocess.call([sys.executable, str(test)])
        if rc != 0:
            failed.append(test.name)
    print(f"\n{'=' * 64}")
    if failed:
        print(f"FAILED {len(failed)}/{len(tests)}: {', '.join(failed)}")
        return 1
    print(f"ALL PASS ({len(tests)} test files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
