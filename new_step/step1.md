# Step 1：先让 Python 项目运行起来

[教程目录](README.md) · [下一篇](step2.md)

## 本篇目标

这一篇只做三件事：选对 Python、建立最小目录、打印资料目录。暂时不用理解向量数据库、HTTP 或 Unity，也不下载模型。

最终你应能运行一条命令，看到类似输出：

```json
{"documents_dir": "C:\\Users\\qqcom\\PycharmProjects\\Rag\\data\\documents"}
```

JSON 换行和反斜杠的显示方式可以不同。这里要确认路径指向当前项目。

## 1. 先明确文件应该放在哪里

`new_step` 只存教程。你手敲的 Python 代码依然放在项目根目录下的 `backend`，不要放进 `new_step/backend`。

本套教程采用同一个项目根目录：

```text
C:\Users\qqcom\PycharmProjects\Rag
```

如果你之前跟旧教程写过代码，先保留一份自己的备份。新旧教程的配置和部分函数不同，应选择一套顺序完成，不能跳着混用。编写这些 Markdown 不会自动替你修改现有源码。

## 2. 在 PyCharm 中确认解释器

1. 用 PyCharm 打开整个 `Rag` 文件夹。
2. 在 Settings → Project → Python Interpreter 中选择已有解释器：`Rag\.venv\Scripts\python.exe`。
3. 打开 PyCharm 底部 Terminal，确认是 PowerShell。
4. 执行：

```powershell
Set-Location 'C:\Users\qqcom\PycharmProjects\Rag'
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip --version
```

教程沿用 Python 3.11，已有环境记录为 3.11.9。`pip` 路径应该属于这个 `.venv`。如果解释器不能启动，先在 PyCharm 中修正解释器路径，再继续；不要先删除已有 `.venv`。

命令中的 `-m` 表示“运行一个 Python 模块”。后面一直用同一个解释器路径，减少“包明明装了却找不到”的问题。

## 3. 创建最小目录

在 PyCharm 左侧项目树中右键新建目录。已有目录直接保留：

```text
backend/
    __init__.py
    app/
        __init__.py
    scripts/
        __init__.py
    tests/
        __init__.py
data/
    documents/
        campus/
```

四个 `__init__.py` 都是空文件。它们让 Python 明确把目录当作包。此时不必提前创建数据库、日志、模型和 Unity 的所有目录；到用到时再建。

## 4. 只安装配置所需的依赖

新建 `backend/requirements.txt`：

<!-- file: backend/requirements.txt -->
```text
pydantic==2.11.7
pydantic-settings==2.9.1
```

执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

`requirements.txt` 是本项目依赖清单，后面会逐步扩展。版本沿用原项目的教学基线，不表示它们是最新版本；如果已有环境安装了更多包，不需要为了本篇卸载它们。

## 5. 写最小配置

新建 `backend/.env.example`：

<!-- file: backend/.env.example -->
```dotenv
DOCUMENTS_DIR=data/documents
```

在 PyCharm 中复制一份为 `backend/.env`。两者区别：

- `.env.example` 是可以分享的配置样例。
- `.env` 是本机实际读取的配置。后面调整参数改这份。

新建 `backend/app/config.py`：

<!-- file: backend/app/config.py -->
```python
from pathlib import Path
import json
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / 'backend' / '.env',
        env_file_encoding='utf-8',
    )
    documents_dir: Path = Path('data/documents')


def load_settings():
    settings = Settings()
    settings.documents_dir = (ROOT / settings.documents_dir).resolve()
    return settings


if __name__ == '__main__':
    settings = load_settings()
    print(json.dumps(settings.model_dump(mode='json'), ensure_ascii=False, indent=2))
```

先对照理解四个位置：

| 代码 | 作用 |
| --- | --- |
| `ROOT = ...parents[2]` | 从 `backend/app/config.py` 向上找到项目根目录 |
| `documents_dir` | 资料目录的默认值 |
| `Settings()` | 读取 `.env`，并把配置转换成对应类型 |
| `(ROOT / ...).resolve()` | 把相对路径变为绝对路径 |

这里只配置资料目录。第 3、4、5、7 篇会在首次需要时补充对应参数。

## 6. 运行第一次结果

```powershell
.\.venv\Scripts\python.exe -m backend.app.config
```

也可以创建 PyCharm Run Configuration：选择 Python → Module name 填 `backend.app.config`，Working directory 填项目根目录，解释器选当前 `.venv`。

试着将 `.env` 的目录改为 `data/example`，再次运行，观察输出变化；然后恢复 `data/documents`。打印路径不会创建资料，也不会训练任何模型。

同名系统环境变量也可能影响设置。如果你改了 `.env` 却没生效，先检查是不是改到了 `.env.example`，再检查是否存在同名环境变量。

## 7. 保留本机文件的边界

如果根目录已有 `.gitignore`，将下面规则补充进去，不要覆盖你原有的规则；没有就新建：

```gitignore
.venv/
.idea/
__pycache__/
*.pyc
backend/.env
models/
data/documents/
data/evaluations/*report.json
unity-client/Library/
unity-client/Temp/
unity-client/Obj/
unity-client/Logs/
unity-client/UserSettings/
unity-client/Builds/
```

保留 `.env.example` 和 Unity 的 `.meta` 文件。教程里的合成资料可以公开；以后你自己的资料是否提交，另行决定。

## 为什么这么设计

先拿到一个能运行的最小项目，再逐章增加东西，你就能知道每个模块解决什么问题。配置统一从一个地方读取，后面换资料目录时不必修改多个文件；这份配置暂时只有一个字段，避免第一天就面对十几个尚不理解的参数。

## 排查与验收

| 现象 | 优先检查 |
| --- | --- |
| `No module named backend` | 终端是否在根目录；是否建了空 `__init__.py` |
| 找不到 `pydantic_settings` | 是否使用本篇指定的解释器安装 |
| 出现多余配置字段错误 | 旧教程 `.env` 是否留着本篇没有定义的字段；按本篇样例整理 |
| Python 无法启动 | PyCharm 解释器路径或 Windows 执行权限，先修环境 |

- [ ] 能解释 `.env` 与 `.env.example` 的区别。
- [ ] 能打印正确资料路径。
- [ ] 知道后续代码不放在 `new_step` 目录里。

---

完成验收后进入 [step2](step2.md)。
