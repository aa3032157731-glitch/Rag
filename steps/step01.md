# Step 01：准备环境，创建配置模块

## 目标与前置条件

完成后可以在终端运行 `backend.app.config`，看到资料目录、设备与数据库地址。本篇不启动数据库、不下载 BGE-M3。

已有：项目根目录、`.idea`、`.venv`、`plan.md`。不要删除它们。暂时关闭 Unity，先专心完成 Python。

## 1. 在 PyCharm 选择解释器

1. File → Open，选择 `C:\Users\qqcom\PycharmProjects\Rag`。
2. Settings → Project → Python Interpreter（不同版本入口可能显示为 Python Interpreter）。
3. 选择 Existing environment，解释器填入 `C:\Users\qqcom\PycharmProjects\Rag\.venv\Scripts\python.exe`。
4. 打开底部 Terminal，确认是 PowerShell，执行：

```powershell
Set-Location 'C:\Users\qqcom\PycharmProjects\Rag'
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip --version
```

应看到 Python 3.11.x，pip 路径位于当前 `.venv`。如果不是，先修解释器，不继续安装。

## 2. 创建目录和空文件

在左侧根目录右键 → New → Directory，创建下列目录。斜杠表示层级，不是文件名中的字符：

```text
backend/app
backend/scripts
backend/tests
data/documents/campus
data/manifests
data/evaluations
models
logs
infra
docs
```

分别在 `backend`、`backend/app`、`backend/scripts`、`backend/tests` 内 New → Python File，创建 `__init__.py`，内容为空。这些文件让 Python 明确把目录视作包。`unity-client` 留到 step09 由 Unity Hub 创建。

## 3. 新建依赖清单

文件：`backend/requirements.txt`。以下是固定的教学基线；GPU 版 torch 到 step04 单独说明。主要框架版本已核对 PyPI 发布记录，但不代表你的全部依赖组合已安装验证。

<!-- file: backend/requirements.txt -->
```text
fastapi==0.115.12
uvicorn==0.34.3
pydantic==2.11.7
pydantic-settings==2.9.1
qdrant-client==1.14.3
FlagEmbedding==1.3.5
transformers==4.51.3
sentence-transformers==4.1.0
peft==0.15.2
numpy==1.26.4
pytest==8.3.5
httpx==0.28.1
```

终端 A，根目录执行。安装需要联网，模型权重还不会在这一步下载：

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

`pip check` 应报告没有依赖冲突。如果失败，保留完整错误，不通过盲目 `--upgrade` 把固定版本全部换掉。安装索引说明见 [PyTorch 官方历史版本](https://pytorch.org/get-started/previous-versions/)。

## 4. 新建环境模板

文件：`backend/.env.example`。然后复制一份命名为 `backend/.env`。PyCharm New → File 可创建点号开头文件。两份初始内容相同；以后修改本机配置只改 `.env`。

<!-- file: backend/.env.example -->
```dotenv
DOCUMENTS_DIR=data/documents
MODEL_CACHE=models/huggingface
MODEL_NAME=BAAI/bge-m3
MODEL_REVISION=5617a9f61b028005a4858fdac845db406aefb181
DEVICE=cpu
FP16=false
BATCH_SIZE=1
MAX_TOKENS=1024
CHUNK_SIZE=600
CHUNK_OVERLAP=100
QDRANT_URL=http://127.0.0.1:6333
QDRANT_ALIAS=rag_active
TOP_K=5
MAX_CONTEXT_CHARS=4000
MIN_SCORE=
```

`MODEL_REVISION` 固定本次核对的模型仓库版本；改模型必须重新建立索引。`MIN_SCORE` 留空表示尚未标定，不代表所有返回片段都相关。配置名称是对 `plan.md` 的具体化，例如 `DEVICE` 对应计划中的 `EMBEDDING_DEVICE`；后续全部代码统一使用这里的名字。

## 5. 新建配置代码

文件：`backend/app/config.py`，完整内容如下。

<!-- file: backend/app/config.py -->
```python
from pathlib import Path
import json
import os

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / 'backend' / '.env',
        env_file_encoding='utf-8',
        extra='forbid',
    )
    documents_dir: Path = Path('data/documents')
    model_cache: Path = Path('models/huggingface')
    model_name: str = 'BAAI/bge-m3'
    model_revision: str = '5617a9f61b028005a4858fdac845db406aefb181'
    device: str = 'cpu'
    fp16: bool = False
    batch_size: int = 1
    max_tokens: int = 1024
    chunk_size: int = 600
    chunk_overlap: int = 100
    qdrant_url: str = 'http://127.0.0.1:6333'
    qdrant_alias: str = 'rag_active'
    top_k: int = 5
    max_context_chars: int = 4000
    min_score: float | None = None

    @field_validator('min_score', mode='before')
    @classmethod
    def empty_score(cls, value):
        return None if value == '' else value

    @model_validator(mode='after')
    def validate_values(self):
        if self.device not in ('cpu', 'cuda'):
            raise ValueError('DEVICE 只能是 cpu 或 cuda')
        if self.device == 'cpu' and self.fp16:
            raise ValueError('CPU 基线请设置 FP16=false')
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError('必须 0 <= CHUNK_OVERLAP < CHUNK_SIZE')
        if not 16 <= self.chunk_size <= 10000:
            raise ValueError('CHUNK_SIZE 必须在 16～10000')
        if not 32 <= self.max_tokens <= 8192:
            raise ValueError('MAX_TOKENS 必须在 32～8192')
        if self.batch_size < 1 or not 1 <= self.top_k <= 10:
            raise ValueError('BATCH_SIZE >= 1，TOP_K 在 1～10')
        if not 64 <= self.max_context_chars <= 20000:
            raise ValueError('MAX_CONTEXT_CHARS 必须在 64～20000')
        if self.min_score is not None and not -1 <= self.min_score <= 1:
            raise ValueError('MIN_SCORE 必须留空或在 -1～1')
        if not self.qdrant_url.startswith(('http://', 'https://')):
            raise ValueError('QDRANT_URL 必须包含 http:// 或 https://')
        for name in ('documents_dir', 'model_cache'):
            value = getattr(self, name)
            setattr(self, name, (ROOT / value).resolve())
        return self

    def prepare_cache(self):
        self.model_cache.mkdir(parents=True, exist_ok=True)
        os.environ['HF_HOME'] = str(self.model_cache)


def load_settings():
    return Settings()


if __name__ == '__main__':
    settings = load_settings()
    print(json.dumps(settings.model_dump(mode='json'), ensure_ascii=False, indent=2))
```

### 看懂这段代码

- `Path(__file__)` 是当前源文件的位置，`parents[2]` 向上找到 `Rag`，因此换终端目录不会找错资料。
- `BaseSettings` 将默认值、`.env` 和系统环境变量转换成有类型的配置；同名系统环境变量会覆盖 `.env`，排错时注意这一点。
- `int`、`bool`、`Path` 是类型约束；`float | None` 表示分数可以是数字，也可以未设置。
- `field_validator` 将空字符串转换为 `None`；`model_validator` 检查字段之间的关系。
- `prepare_cache()` 在真正加载模型前调用。本篇只检查配置，不创建或下载模型。
- `if __name__ == '__main__'` 让此文件既能被导入，也能单独作为模块运行。

## 6. 新建 Git 忽略文件

文件：根目录 `.gitignore`。

<!-- file: .gitignore -->
```gitignore
.venv/
.idea/
__pycache__/
.pytest_cache/
*.pyc
backend/.env
infra/.env
models/
logs/
data/documents/
data/manifests/
data/evaluations/*report*.json
unity-client/[Ll]ibrary/
unity-client/[Tt]emp/
unity-client/[Oo]bj/
unity-client/[Ll]ogs/
unity-client/[Uu]ser[Ss]ettings/
unity-client/[Bb]uilds/
unity-client/*.csproj
unity-client/*.sln
```

不忽略 `.env.example`，也不忽略 Unity `.meta`。真实知识文档默认不提交；后续测试使用临时合成文件。

## 7. 运行并验收

终端 A，根目录：

```powershell
.\.venv\Scripts\python.exe -m backend.app.config
```

输出应为 JSON，`documents_dir` 是本机绝对路径、`device` 是 `cpu`、`min_score` 是 `null`。

PyCharm 也可通过 Run → Edit Configurations → Add Python，选择 Module name：`backend.app.config`；Working directory：项目根目录；Interpreter：已有 `.venv`。点击运行，结果应相同。

故意把 `.env` 中 `CHUNK_OVERLAP` 改为 `600`，运行应出现明确的校验错误；测试后改回 `100`。

| 现象 | 检查与解决 |
| --- | --- |
| No module named backend | 终端不在根目录，或漏建 `__init__.py` |
| No module named pydantic_settings | 安装到了其他 Python；使用上面的完整解释器路径 |
| env 配置没变化 | 是否改了 `.env.example` 而不是 `.env`；系统环境变量是否覆盖 |
| 安装失败 | 检查网络、Python 3.11 和磁盘空间，保留原始 pip 错误 |

- [ ] 虚拟环境路径正确，未重建已有环境。
- [ ] `pip check` 无冲突。
- [ ] 配置正常输出，错误配置能够被拒绝。
- [ ] 已将测试改动恢复。

下一篇：[step02：读取三种文档](step02.md)。
