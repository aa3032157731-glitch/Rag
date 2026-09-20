# Step 04：运行 BGE-M3，测量本机向量性能

## 目标与前置条件

完成 step01～03；网络能访问模型仓库，有足够磁盘空间。本篇首次下载模型，可能需要较长时间；不需要 Qdrant。

这里测的是向量模型，不是聊天模型。BGE-M3 将文本变成数字，不会回答“几点关门”。官方模型与示例见 [BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)。本教程仅使用 dense 输出，维度为 1024。

## 1. 确认 CPU 基线

终端 A，根目录：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print('CUDA:', torch.cuda.is_available())"
```

step01 安装的是 CPU 版，显示 `False` 是正常情况，不代表显卡坏了。`.env` 保持 `DEVICE=cpu`、`FP16=false`、`BATCH_SIZE=1`。

关闭其他会加载模型的程序。我们让每个进程只创建一个 EmbeddingService，先不要同时开几个测试终端。

## 2. 新建 `backend/app/embedding.py`

<!-- file: backend/app/embedding.py -->
```python
import json
from pathlib import Path

from backend.app.models import digest


class EmbeddingService:
    dimension = 1024

    def __init__(self, settings):
        self.settings = settings
        settings.prepare_cache()
        # 延迟导入：先设置缓存目录，再导入模型库。
        import numpy as np
        import torch
        from huggingface_hub import snapshot_download
        from FlagEmbedding import BGEM3FlagModel

        if settings.device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('配置了 CUDA，但当前 PyTorch 无法使用 GPU')
        self.np = np
        snapshot = snapshot_download(
            repo_id=settings.model_name,
            revision=settings.model_revision,
            cache_dir=str(settings.model_cache / 'hub'),
            allow_patterns=[
                '*.json', '*.model', 'tokenizer*',
                'pytorch_model.bin', 'model.safetensors',
                'colbert_linear.pt', 'sparse_linear.pt',
                'sentencepiece*', 'special_tokens_map.json',
            ],
        )
        self.revision = Path(snapshot).name
        self.model = BGEM3FlagModel(
            snapshot, devices=settings.device,
            use_fp16=settings.fp16, normalize_embeddings=True,
        )
        self.tokenizer = self.model.tokenizer

    def count_tokens(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=True, truncation=False))

    def encode_documents(self, texts):
        if not texts:
            return []
        for text in texts:
            if not text.strip():
                raise ValueError('不能编码空文字')
            count = self.count_tokens(text)
            if count > self.settings.max_tokens:
                raise ValueError(f'编码输入 {count} tokens，超过上限；先分段，不要静默截断')
        output = self.model.encode(
            texts, batch_size=self.settings.batch_size,
            max_length=self.settings.max_tokens,
            return_dense=True, return_sparse=False, return_colbert_vecs=False,
        )['dense_vecs']
        vectors = self.np.asarray(output, dtype=self.np.float32)
        if vectors.shape != (len(texts), self.dimension):
            raise RuntimeError(f'意外向量形状：{vectors.shape}')
        if not self.np.isfinite(vectors).all():
            raise RuntimeError('向量出现 NaN 或 Inf')
        norms = self.np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms <= 0).any():
            raise RuntimeError('向量长度为零')
        return (vectors / norms).tolist()

    def encode_query(self, query):
        return self.encode_documents([query])[0]

    def fingerprint(self):
        value = {
            'model': self.settings.model_name,
            'revision': self.revision,
            'dimension': self.dimension,
            'max_tokens': self.settings.max_tokens,
            'fp16': self.settings.fp16,
            'normalized': True,
            'input_format': 'title80-section80-text-v1',
            'chunk_size': self.settings.chunk_size,
            'chunk_overlap': self.settings.chunk_overlap,
        }
        return digest(json.dumps(value, sort_keys=True))
```

### 解释

- `snapshot_download` 固定 revision 并返回本地目录；后续运行复用缓存，仍可能检查下载状态。
- `count_tokens` 不截断，因此能发现超长输入。文档超长会在导入时二次分段；超长问题会提示缩短。
- `encode_documents` 支持多条输入，内部按 batch 处理；`encode_query` 复用相同算法，避免问题和文档来自不同向量空间。
- 归一化把向量长度变成 1。我们校验维度和有限数值，避免坏数据进入数据库。
- `fingerprint` 是索引兼容标识。改变模型或关键参数后，后端会要求重新导入，不会拿新问题向量查询旧索引。
- 没有自动把“CUDA 失败”悄悄改成 CPU，避免性能问题被隐藏。

## 3. 新建性能测试脚本

文件：`backend/scripts/benchmark_embedding.py`。确认 `backend/scripts/__init__.py` 已创建。

<!-- file: backend/scripts/benchmark_embedding.py -->
```python
import json
import statistics
import time
from datetime import datetime, timezone

from backend.app.config import ROOT, load_settings
from backend.app.embedding import EmbeddingService


def main():
    settings = load_settings()
    start = time.perf_counter()
    model = EmbeddingService(settings)
    loading = time.perf_counter() - start
    texts = [
        '图书馆每天八点开门，晚上十点关门。',
        '图书馆夜间什么时候闭馆？',
        '第一食堂二楼有素食窗口。',
    ]
    vectors = model.encode_documents(texts)
    cosine = lambda a, b: sum(x * y for x, y in zip(a, b))
    print('维度：', len(vectors[0]))
    print('相关文本分数：', cosine(vectors[0], vectors[1]))
    print('无关文本分数：', cosine(vectors[0], vectors[2]))
    times = []
    for index in range(30):
        start = time.perf_counter()
        model.encode_query(texts[index % len(texts)])
        times.append((time.perf_counter() - start) * 1000)
    report = {
        'at': datetime.now(timezone.utc).isoformat(),
        'device': settings.device,
        'fingerprint': model.fingerprint(),
        'download_and_load_seconds': round(loading, 3),
        'query_count': len(times),
        'p50_ms': round(statistics.median(times), 2),
        'p95_ms': round(sorted(times)[28], 2),
        'samples_ms': times,
    }
    destination = ROOT / 'docs' / 'embedding-benchmark.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
```

终端 A：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.benchmark_embedding
```

首次加载时间包含下载，不用它评价每轮交互速度。输出维度应为 1024，分数只是相似度，不是正确概率。通常相关文本应更接近；如果不是，先核对代码、模型与样本。

打开任务管理器 → 详细信息，记录 Python 的内存使用；如测试 GPU，另观察显存。把机器条件、峰值与结果说明手动记入 `docs/benchmark.md`。脚本只统计编码时间，不冒充统计了内存峰值。

## 4. GPU 可选分支：通过 CPU 验证后再尝试

已发现本机 RTX 3050 Laptop 4GB，驱动版本曾读到 546.30；这不保证符合你后来安装的 PyTorch CUDA 版本。先运行 `nvidia-smi`，按 [PyTorch 官方安装说明](https://pytorch.org/get-started/locally/) 和 NVIDIA 对应运行时要求确认兼容性，不在不知道驱动要求时直接替换。

GPU 分支的步骤：

1. 保存 CPU 测试报告，不运行导入或 FastAPI。
2. 在本项目解释器中安装经官方版本表核对的 CUDA PyTorch；它会替换 CPU 包。不要把系统 CUDA Toolkit 版本当成 PyTorch 自带运行时版本。
3. 运行 `torch.cuda.is_available()`，只有 True 才继续。
4. 将 `.env` 改为 `DEVICE=cuda`、`FP16=true`、`BATCH_SIZE=1`，重新运行 benchmark。
5. 记录速度和显存。显存不足时先关闭 Unity、其他模型进程；仍不足则恢复 CPU 配置与 CPU 包。
6. 后续如已有索引，切换 FP16 后必须重建，因为指纹会变化。

主教程继续以 CPU 为基线，GPU 不是完成教程的前置条件。由于尚未测试 GPU 兼容组合，这里不提供未经验证的“必定可用”驱动安装指令。

## 5. 排查与验收

| 现象 | 检查与处理 |
| --- | --- |
| 下载连接失败 | 查看模型仓库能否访问、代理与磁盘空间；重试会利用缓存，不删整个缓存 |
| transformers / peft 导入错误 | 比较 step01 固定版本并执行 pip check，避免混装最新版本 |
| 很慢 | 区分首次下载与热编码；确认没有每次问题都创建新模型 |
| token 超限 | 增加分段，不删除输入校验；问题过长则缩短 |
| 显存不足 | batch=1、降低输入长度、停止重复模型进程或使用 CPU |

- [ ] 生成有效 1024 维向量。
- [ ] 实际记录了 30 次热查询 p50/p95。
- [ ] 相似度样例与预期基本一致，没有把分数当概率。

下一篇：[step05：部署 Qdrant](step05.md)。
