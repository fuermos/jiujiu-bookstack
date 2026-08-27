#!/usr/bin/env python3
"""pipeline_worker.py - 单次执行模式 pipeline worker (铲屎官 2026-08-25 钓定)

用法:
    python3 scripts/pipeline_worker.py         # 拉一个 queued 跑, 立刻返回
    python3 scripts/pipeline_worker.py --loop  # 守护模式 (一般不用, 推茬 cron)

设计思路:
- OpenClaw cron 每 1 分钟触发一次这个脚本
- 检查: 有无 stale running (超时) → 标 failed
- 检查: 有无活跃 running (started_at < 30min) → 跳过 (不重复)
- 检查: 有无 queued → 启 subprocess 跑一个 (前台等完成)
- 完成 → 写 status

为什么不用 daemon thread:
- 之前 streamlit 里启动 daemon thread 难调, module-level 在 streamlit import 时不执行
- cron 触发独立进程 → 清清淅淳、不会随 streamlit 重启丢失
"""
import sys
import os
import re
import time
import argparse
import subprocess
from pathlib import Path

# 初始化 path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from db import init_pool, get_cursor
from config_loader import load_config

STALE_MINUTES = 30  # running 超过 30 分钟认为死掉了


def cleanup_stale_jobs() -> int:
    """清理超时 running jobs (started_at < now - 30min)"""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET status='failed', error='timeout: running > 30min', "
            "finished_at=now() WHERE status='running' "
            "AND started_at < now() - interval '30 minutes' RETURNING id"
        )
        rows = cur.fetchall()
    return len(rows)


def has_active_running_job() -> bool:
    """检查是否有活跃的 running job (< 30min, 说明有 worker 在跑)"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM pipeline_jobs WHERE status='running' "
            "AND started_at > now() - interval '30 minutes' LIMIT 1"
        )
        return cur.fetchone() is not None


def parse_progress(log_text: str) -> tuple[str, int, int]:
    """从日志文本解析 (current_step, progress_done, progress_total)"""
    step_match = re.search(r'Step\s*(\d+(?:\.\d+)?)\s*:?\s*([^\n]+)', log_text)
    if step_match:
        step_label = f"Step {step_match.group(1)}: {step_match.group(2)[:30]}"
    else:
        step_label = 'running'
    prog_match = re.search(r'(?:进度\s*|progress\s*)(\d+)\s*/\s*(\d+)', log_text)
    if prog_match:
        return step_label, int(prog_match.group(1)), int(prog_match.group(2))
    return step_label, 0, 0


def update_progress(job_id: int, step: str, prog: int, total: int) -> None:
    try:
        with get_cursor() as cur:
            cur.execute(
                "UPDATE pipeline_jobs SET current_step=%s, step_progress=%s, step_total=%s WHERE id=%s",
                (step, prog, total, job_id),
            )
    except Exception as e:
        print(f'  ⚠️ update_progress failed: {e}', file=sys.stderr, flush=True)


def process_one() -> bool:
    """拉一个 queued job, 跑完。返回 True=跑了, False=没 job"""
    # 拿一个 queued
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, book_id, book_name, file_path, log_path FROM pipeline_jobs "
            "WHERE status='queued' ORDER BY id LIMIT 1"
        )
        job = cur.fetchone()
    if not job:
        return False

    job_id = job['id']
    book_name = job['book_name']
    log_path = ROOT / job['log_path']
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 标记 running
    with get_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET status='running', started_at=now(), current_step='started', "
            "step_progress=0, step_total=0 WHERE id=%s",
            (job_id,),
        )

    print(f'🚀 job #{job_id} 开始: {book_name}', flush=True)

    # 启动 subprocess (写日志到 log_path)
    with open(log_path, 'wb', 0) as logf:
        proc = subprocess.Popen(
            ['python3', '/app/scripts/pipeline.py', job['file_path'], '--force'],
            stdout=logf, stderr=subprocess.STDOUT,
            cwd='/app',
        )

    # 监控进度 + 等进程退出
    last_size = 0
    last_update = 0
    while proc.poll() is None:
        time.sleep(3)
        try:
            cur_size = log_path.stat().st_size
            if cur_size > last_size:
                with open(log_path, 'rb') as f:
                    f.seek(last_size)
                    new_text = f.read().decode('utf-8', errors='replace')
                last_size = cur_size
                step, prog, total = parse_progress(new_text)
                now_t = time.time()
                if now_t - last_update > 5:  # 限流 5s
                    update_progress(job_id, step, prog, total)
                    last_update = now_t
        except Exception:
            pass

    # 进程退出, 写 status
    rc = proc.returncode
    status = 'completed' if rc == 0 else 'failed'
    error_msg = None
    if rc != 0:
        try:
            tail = log_path.read_text(encoding='utf-8', errors='replace')[-1000:]
            # 提取最后几行错误
            error_msg = tail[-500:] if tail else f'exit code {rc}'
        except Exception:
            error_msg = f'exit code {rc}'

    try:
        full_text = log_path.read_text(encoding='utf-8', errors='replace')
        step, prog, total = parse_progress(full_text)
    except Exception:
        step, prog, total = ('done', 0, 0)

    with get_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET status=%s, finished_at=now(), error=%s, "
            "current_step=%s, step_progress=%s, step_total=%s WHERE id=%s",
            (status, error_msg, step, prog, total, job_id),
        )

    print(f'{"✅" if rc == 0 else "❌"} job #{job_id} 完成: {status} (rc={rc})', flush=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--loop', action='store_true', help='守护模式 (一般不用)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    cfg = load_config()
    init_pool(cfg['database'])

    if args.loop:
        print('🔁 worker 进入守护模式 (sleep 10s 间隔)', flush=True)
        while True:
            try:
                # 0. stale 清理
                n = cleanup_stale_jobs()
                if n and args.verbose:
                    print(f'  🧹 清 {n} 个 stale jobs', flush=True)
                # 1. 有活跃 running → 跳过
                if has_active_running_job():
                    time.sleep(15)
                    continue
                # 2. 拉 queued 跑
                if not process_one():
                    time.sleep(10)
            except KeyboardInterrupt:
                print('\n👋 worker 退出', flush=True)
                break
            except Exception as e:
                print(f'❌ worker_loop error: {e}', file=sys.stderr, flush=True)
                import traceback; traceback.print_exc()
                time.sleep(30)
    else:
        # 单次模式 (cron 用)
        # 0. stale 清理
        n = cleanup_stale_jobs()
        if n and args.verbose:
            print(f'  🧹 清 {n} 个 stale jobs', flush=True)
        # 1. 有活跃 running → 跳过
        if has_active_running_job():
            print('⏭️ 已有活跃 running job, 跳过本次')
            return
        # 2. 拉 queued 跑
        if not process_one():
            if args.verbose:
                print('💤 无 queued job')


if __name__ == '__main__':
    main()