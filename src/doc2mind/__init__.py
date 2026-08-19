"""DocMind — 轻量向量知识库工具。

把任意文档（PDF / Word / Excel / PPT / Markdown / HTML / 图片 / 代码）
转成语义分块、向量索引和可检索的知识库。全部计算在本地完成。
"""

import os

# 限制底层 BLAS/OMP 线程数，防止 Windows 多线程并发时 OpenBLAS 内存分配失败 (OpenBLAS error: Memory allocation failed)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

__version__ = "1.0.1"
__all__ = ["__version__"]
