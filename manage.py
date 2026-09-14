#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "无法导入Django。你确定它已经安装，并且在你的 PYTHONPATH 环境变量中是可用的吗？"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
