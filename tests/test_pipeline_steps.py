#!/usr/bin/env python3
"""test_pipeline_steps.py - 测试 pipeline.py --steps 参数过滤生效

bug_2026_08_29_pipeline_steps_unwired:
- 主人发现: python3 scripts/pipeline.py --book-id 28 --steps embed,classify,dedup
  实际还是跑全部 8 步 (跑到 step 4 skill 才挂)
- 根因: main() 解析了 args.steps 但没传给 run_full_pipeline()
- 修法: pipeline.py 加 VALID_STEPS + steps 参数 + 每步前 check
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


class TestPipelineStepsArgument:
    """验证 --steps 参数能正确过滤步骤"""

    def test_valid_steps_set_contains_all_9(self):
        """VALID_STEPS 必须包含 9 个合法值"""
        from pipeline import VALID_STEPS
        expected = {'import', 'embed', 'classify', 'mindmap', 'skill', 'script', 'split', 'summary', 'dedup'}
        assert VALID_STEPS == expected, f"VALID_STEPS 不匹配: {VALID_STEPS} vs {expected}"

    def test_run_full_pipeline_accepts_steps_kwarg(self):
        """run_full_pipeline 必须接受 steps kwarg"""
        import inspect
        from pipeline import run_full_pipeline
        sig = inspect.signature(run_full_pipeline)
        assert 'steps' in sig.parameters, f"run_full_pipeline 缺 steps 参数: {sig}"
        # steps 参数默认 None
        assert sig.parameters['steps'].default is None

    def test_steps_unknown_raises_valueerror(self):
        """未知的 step 名应该报错"""
        from pipeline import run_full_pipeline
        with pytest.raises(ValueError, match="未知 step"):
            run_full_pipeline(28, {}, None, steps=['invalid_step_xyz'])

    def test_main_passes_steps_to_pipeline(self):
        """main() 必须把 --steps 解析后传给 run_full_pipeline

        间接验证: 通过调用 main 解析 args, 检查传给 run_full_pipeline 的 steps 参数
        """
        # 用 patch 拦截 run_full_pipeline 调用, 检查 steps 参数
        import pipeline as pipeline_mod
        original = pipeline_mod.run_full_pipeline
        captured = {}
        def spy(book_id, config, llm, force=False, books_mode=False, steps=None):
            captured['steps'] = steps
            captured['book_id'] = book_id
            captured['books_mode'] = books_mode
        pipeline_mod.run_full_pipeline = spy
        try:
            # 模拟命令行: pipeline.py --book-id 28 --steps embed,classify,dedup
            test_argv = ['pipeline.py', '--book-id', '28', '--steps', 'embed,classify,dedup']
            import sys as _sys
            _sys.argv = test_argv
            pipeline_mod.main()
        finally:
            pipeline_mod.run_full_pipeline = original

        assert captured.get('steps') == ['embed', 'classify', 'dedup'], \
            f"steps 没正确传递: {captured.get('steps')}"
        assert captured.get('book_id') == 28
        assert captured.get('books_mode') is False  # --book-id 模式 → books_mode=False

    def test_main_no_steps_means_none(self):
        """不传 --steps 时, run_full_pipeline 应该收到 steps=None"""
        import pipeline as pipeline_mod
        original = pipeline_mod.run_full_pipeline
        captured = {}
        def spy(book_id, config, llm, force=False, books_mode=False, steps=None):
            captured['steps'] = steps
        pipeline_mod.run_full_pipeline = spy
        try:
            import sys as _sys
            _sys.argv = ['pipeline.py', '--book-id', '28']
            pipeline_mod.main()
        finally:
            pipeline_mod.run_full_pipeline = original

        assert captured.get('steps') is None, f"不传 --steps 应为 None: {captured.get('steps')}"


if __name__ == "__main__":
    print("⚠️  请用 pytest 跑: pytest tests/test_pipeline_steps.py -v")
    sys.exit(1)
