import json
import os
import subprocess
import sys


def main():
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input") or {}
    tool_response = data.get("tool_response") or {}
    file_path = tool_input.get("file_path") or tool_response.get("filePath") or ""
    base = os.path.basename(file_path)

    if base not in ("engine.py", "state.py"):
        return

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "tests", "-v"],
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr

    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-80:])
        print(tail, file=sys.stderr)
        fails = [
            line for line in output.splitlines()
            if line.startswith("FAIL:") or line.startswith("ERROR:")
        ]
        summary = "; ".join(fails)[:800]
        msg = f"Тесты упали после правки {base}: {summary}"
    else:
        msg = f"Тесты зелёные после правки {base}"

    print(json.dumps({"systemMessage": msg}))


if __name__ == "__main__":
    main()
