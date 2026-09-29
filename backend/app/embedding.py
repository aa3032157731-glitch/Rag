import json
from pathlib import Path

from backend.app.models import digest

class EmbeddingService:
    dimension = 1024

    def __init__(self, settings):
        self.settings = settings
        settings.prepare_cache()
        import numpy as np
        import torch
        from huggingface_hub import snapshot_download
        from FlagEmbedding import BGEM3FlagModel
        if settings.device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('配置了CUDA, 但当前PyTorch无法使用GPU')
        self.np = np
        snapshot = snapshot_download(
            repo_id=settings.model_name,
            revision=settings.model_revision,
            cache_dir=str(settings.model_cache / 'hub'),
            allow_patterns=[
                '*.json', '*.model', 'tokenizer',
                'pytorch_model.bin', 'model.safetensors',
                'colbert_linear.pt', 'sparse_linear.pt',
                'sentencepiece*', 'special_tokens_map.json',
            ],
        )
        self.revision = Path(snapshot).name
        self.model = BGEM3FlagModel(
            snapshot,
            devices=settings.device,
            use_fp16=settings.fp16,
            normalize_embeddings=True,
        )
        self.tokenizer = self.model.tokenizer



    def count_tokens(self, text):
        return len(
            self.tokenizer.encode(
                text,
                add_special_tokens=True,
                truncation=False,
            )
        )

    def encode_documents(self, texts):
        if not texts:
            return []
        for text in texts:
            if not text.strip():
                raise ValueError('不能编码空文字')
            count = self.count_tokens(text)
            if count > self.settings.max_tokens:
                raise ValueError(
                    f'编码输入 {count} tokens, 超过上限; 先分段, 不要静默截断'
                )
        output = self.model.encode(
            texts,
            batch_size=self.settings.batch_size,
            max_length=self.settings.max_tokens,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )['dense_vecs']
        vectors = self.np.asarray(output, dtype=self.np.float32)
        if vectors.shape != (len(texts), self.dimension):
            raise RuntimeError(f'意外向量形状: {vectors.shape}')
        if not self.np.isfinite(vectors).all():
            raise RuntimeError('向量出现 NaN 或 Inf')
        norms = self.np.linalg.norm(
            vectors,
            axis=1,
            keepdims=True,
        )
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
