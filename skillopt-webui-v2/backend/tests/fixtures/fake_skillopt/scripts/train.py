#!/usr/bin/env python3
"""假训练器 — 复刻真实 scripts/train.py 的外部契约用于测试：

  * 参数：--config <rel> --cfg-options k=v ...
  * stdout 打印 [EPOCH i/N] / [STEP g/T] / [1/6 ROLLOUT]…[6/6 EVALUATE] 标记
  * out_root 下写 runtime_state.json / history.json / best_skill.md / skills/
  * out_root 已有 runtime_state.json 时自动断点续训

环境变量控制行为（测试用）：
  FAKE_TRAIN_FAIL=1        跑完第 1 步后以退出码 3 失败
  FAKE_TRAIN_STEP_SLEEP    每步 sleep 秒数（默认 0.05；测 stop 时调大）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

STAGES = ["ROLLOUT", "REFLECT", "AGGREGATE", "SELECT", "UPDATE", "EVALUATE"]


def parse_options(options):
    cfg = {}
    for item in options or []:
        key, value = item.split("=", 1)
        cfg[key] = value
    return cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--cfg-options", nargs="*", default=[])
    args = parser.parse_args()

    opts = parse_options(args.cfg_options)
    out_root = Path(opts.get("env.out_root", "outputs/fake"))
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "skills").mkdir(exist_ok=True)

    total_epochs = int(opts.get("train.num_epochs", 2))
    steps_per_epoch = 2
    total_steps = total_epochs * steps_per_epoch
    step_sleep = float(os.environ.get("FAKE_TRAIN_STEP_SLEEP", "0.05"))

    state_file = out_root / "runtime_state.json"
    start_step = 1
    if state_file.is_file():
        state = json.loads(state_file.read_text())
        start_step = state.get("last_completed_step", 0) + 1
        print(f"[RESUME] resuming from step {start_step}", flush=True)

    print(f"config={args.config} out_root={out_root}", flush=True)
    history = []
    history_file = out_root / "history.json"
    if history_file.is_file():
        history = json.loads(history_file.read_text())

    for step in range(start_step, total_steps + 1):
        epoch = (step - 1) // steps_per_epoch + 1
        print(f"[EPOCH {epoch}/{total_epochs}]", flush=True)
        print(f"[STEP {step}/{total_steps}]", flush=True)
        for i, stage in enumerate(STAGES, 1):
            print(f"[{i}/6 {stage}] step {step}", flush=True)
            time.sleep(step_sleep / len(STAGES))
        (out_root / "skills" / f"skill_v{step:04d}.md").write_text(f"# skill v{step}\n")
        history.append({"step": step, "score": 0.5 + step * 0.05})
        history_file.write_text(json.dumps(history))
        state_file.write_text(json.dumps({"last_completed_step": step}))
        if os.environ.get("FAKE_TRAIN_FAIL") and step >= start_step:
            print("[ERROR] injected failure", flush=True)
            sys.exit(3)

    (out_root / "best_skill.md").write_text("# best skill\ndemo content\n")
    print("[DONE] training complete", flush=True)


if __name__ == "__main__":
    main()
