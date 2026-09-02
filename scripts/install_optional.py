#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DocMind 可选扩展安装助手（GPU 加速 / OCR 图片识别）。

由安装器在用户勾选可选任务时调用；相比原来在 .iss [Run] 里直接跑 pip：
  - 多镜像自动回退（清华 → 阿里云 → 官方 PyPI），单一镜像不通不再直接失败；
  - 实时输出 pip 进度（不再 runhidden 隐藏窗口）；
  - 版本与 requirements-gpu.txt / requirements-ocr.txt 保持一致（不跑自由解析）。

用法：
  python install_optional.py gpu     # GPU 加速嵌入（onnxruntime-gpu）
  python install_optional.py ocr     # OCR 图片文字识别（PaddleOCR CPU 版）
"""

import subprocess
import sys

# 与 requirements-gpu.txt / requirements-ocr.txt 保持一致的版本。
# OCR 面向普通最终用户装 CPU 版 paddlepaddle（官方 wheel 自带运行时）；
# 需要 GPU 版（paddlepaddle-gpu）的用户按 requirements-ocr.txt 注释自行安装。
PACKAGES = {
    "gpu": ["onnxruntime-gpu==1.28.0"],
    "ocr": ["paddlepaddle==3.3.1", "paddleocr==3.7.0", "pillow==12.2.0"],
}

# 按顺序回退；国内优先，全部失败最后回官方源
MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.org/simple",
]

NAMES = {"gpu": "GPU 加速包", "ocr": "OCR 图片文字识别"}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PACKAGES:
        print("用法: python install_optional.py gpu|ocr")
        print("可选扩展: " + ", ".join(sorted(PACKAGES)))
        return 2

    mode = sys.argv[1]
    name = NAMES[mode]
    pkgs = PACKAGES[mode]
    print(f"[DocMind] 开始安装{name}（{len(pkgs)} 个包，约需联网下载）…")

    for mirror in MIRRORS:
        print(f"\n[DocMind] 使用镜像: {mirror}")
        cmd = [sys.executable, "-m", "pip", "install", *pkgs, "-i", mirror]
        # 直接继承当前控制台，pip 实时进度可见
        rc = subprocess.call(cmd)
        if rc == 0:
            print(f"\n[DocMind] ✅ {name} 安装完成！")
            return 0
        print(f"[DocMind] 镜像 {mirror} 安装失败（退出码 {rc}），切换下一个镜像重试…")

    print("\n[DocMind] ❌ 所有镜像均失败，请检查网络后重新运行安装器。")
    return 1


if __name__ == "__main__":
    sys.exit(main())