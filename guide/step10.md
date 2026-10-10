# Step 10：评估——RAG 真的比全文注入好吗？

**做完这一步**：你能用数字说出检索的准确率；能在真实资料上比较"全文注入"和"RAG"两种模式；能讲清楚在**你的**数据上，哪种更好、为什么。

前面九步回答的是"能不能跑通"，这一步回答的是"做得好不好"。没有评估，你每调一次参数，都只能凭感觉判断它是变好了还是变坏了。

---

## 1. 练习一：检索评估脚本

### 题目格式

新建 `data/evaluations/questions.jsonl`，每行一道题：

```json
{"question": "图书馆几点关门", "answer": "22:00"}
{"question": "宿舍几点熄灯", "answer": null}
```

- `answer` 填答案在资料原文里的**一小段原文**。只要检索到的某段 `text` 里包含它，就算找到了。这比"来源文件对不对"要严格：同一个文件里可能有好几段，必须找到**含有答案的那一段**才算数。
- `answer` 为 `null`，表示资料里没有答案。对这种题，理想结果是被阈值过滤掉。

先用下面这 16 道题，它们针对的是现在那 3 份合成资料：

```json
{"question": "图书馆几点关门", "answer": "22:00"}
{"question": "图书馆周末开吗", "answer": "周末开放时间相同"}
{"question": "自习区在哪", "answer": "自习区在图书馆二楼"}
{"question": "图书馆在哪", "answer": "明德楼东侧"}
{"question": "早饭几点开始", "answer": "7:00"}
{"question": "第一食堂在哪", "answer": "生活区南侧"}
{"question": "有素食吗", "answer": "素食窗口"}
{"question": "校园卡丢了怎么办", "answer": "服务楼一楼"}
{"question": "补办校园卡要多少钱", "answer": "20 元"}
{"question": "补卡要带什么证件", "answer": "身份证"}
{"question": "网络坏了找谁", "answer": "信息楼 203"}
{"question": "报修网络要提供什么", "answer": "学号"}
{"question": "宿舍几点熄灯", "answer": null}
{"question": "今天天气怎么样", "answer": null}
{"question": "校医院电话是多少", "answer": null}
{"question": "讲个笑话", "answer": null}
```

### 接口

新建 `backend/scripts/evaluate.py`：

```python
def load_cases(path) -> list[dict]:
    """读取 jsonl，每行一个 dict，跳过空行。"""

def judge(case, hits, min_score) -> dict:
    """判断一道题。返回 {'question', 'answerable', 'rank', 'top1', 'ok'}"""

def summarize(rows) -> dict:
    """返回 {'hit@1', 'hit@3', 'kept_by_threshold', 'rejected_unanswerable'}，保留 3 位小数。"""

def main():
    """检索每道题，打印每一行结果和汇总。"""
```

**judge 的规则**

- `top1`：第 1 条的分数；没有结果时记为 0。
- 有答案的题：`rank` 是第一条包含 `answer` 的结果的名次，从 1 开始数，没找到就是 `None`。找到了，并且那一条的分数 ≥ `min_score`，`ok` 才为 True。
- 没答案的题：`rank` 为 `None`；`top1 < min_score` 时 `ok` 为 True，也就是"被正确地拒绝了"。

**summarize 的规则**：前三项**只用有答案的题**来算，最后一项**只用没答案的题**来算。分母不能混在一起，否则加几道没答案的题，Hit@1 就会无缘无故地下降。

**main 的规则**

- 检索时用 `top_k=5`，并且**不要设置 min_score**，把原始结果全部拿回来；阈值只在 `judge` 里使用。这样同一次运行里，你就能看到所有分数，用来决定阈值应该设在哪里。
- 输出按"有没有答案、top1 分数"排序，一行一道题：

```text
ok  0.342  无答案    讲个笑话
XX  0.526  无答案    宿舍几点熄灯
...
ok  0.537  rank=1   有素食吗
```

### 用测试检查

新建 `backend/tests/test_evaluate.py`：

```python
import unittest

from backend.app.models import Hit
from backend.scripts.evaluate import judge, summarize


def hit(text, score):
    return Hit('id', 'x.txt', '标题', '', 'k', text, score)


class EvaluateTests(unittest.TestCase):
    def test_rank_is_first_hit_containing_answer(self):
        hits = [hit('食堂七点开门', 0.7), hit('图书馆 22:00 关门', 0.6)]
        row = judge({'question': '图书馆几点关门', 'answer': '22:00'}, hits, 0.5)
        self.assertEqual(row['rank'], 2)
        self.assertTrue(row['ok'])

    def test_answer_below_threshold_is_not_ok(self):
        row = judge({'question': 'q', 'answer': '22:00'}, [hit('22:00 关门', 0.4)], 0.5)
        self.assertEqual(row['rank'], 1)
        self.assertFalse(row['ok'])

    def test_unanswerable_ok_only_when_rejected(self):
        case = {'question': '天气', 'answer': None}
        self.assertTrue(judge(case, [hit('无关', 0.3)], 0.5)['ok'])
        self.assertFalse(judge(case, [hit('无关', 0.6)], 0.5)['ok'])
        self.assertTrue(judge(case, [], 0.5)['ok'])

    def test_summary_uses_separate_denominators(self):
        rows = [
            {'answerable': True, 'rank': 1, 'ok': True},
            {'answerable': True, 'rank': 3, 'ok': True},
            {'answerable': True, 'rank': None, 'ok': False},
            {'answerable': False, 'rank': None, 'ok': True},
        ]
        summary = summarize(rows)
        self.assertEqual(summary['hit@1'], 0.333)
        self.assertEqual(summary['hit@3'], 0.667)
        self.assertEqual(summary['rejected_unanswerable'], 1)


if __name__ == '__main__':
    unittest.main()
```

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_evaluate -v
.\.venv\Scripts\python.exe -m backend.scripts.evaluate
```

**读结果之前先想一想**：如果 Hit@1 是 100%，能说明这个检索系统很好吗？

---

## 2. 换成真实资料

合成资料只有 5 段，太简单了，评估不出真实水平。现在换成你的数字人真正要用的资料。

### 准备资料

- 至少 **20 份**，内容就是数字人实际要回答的那些，比如学校官网上的办事指南、图书馆、食堂、校医院、宿舍规定等等。
- **只用 `.txt` 格式**。原因是数字人原来的 `KnowledgeBaseStore` 只读 `.txt` 和 `.json`，不读 `.md`；而你的 Python loader 遇到嵌套的 JSON 会直接报错。用 `.txt`，两种模式读到的内容就完全一样，对比才公平。
- **故意放一些容易混淆的资料**。比如几个食堂，各自的开放时间不一样；或者几种证件的补办流程。检索真正的难点就在这里。

### 让两边读同一个目录

把资料放到数字人的知识库目录里：

```text
C:\Users\qqcom\AppData\LocalLow\Camelminger\customDigitalman\VoiceChat\KnowledgeBase
```

然后把 `backend/.env` 里的 `DOCUMENTS_DIR` 改成这个**绝对路径**（用正斜杠）。你的 config 是用 `ROOT / 路径` 来拼接的，遇到绝对路径时会直接使用它。

```text
DOCUMENTS_DIR=C:/Users/qqcom/AppData/LocalLow/Camelminger/customDigitalman/VoiceChat/KnowledgeBase
```

### 准备题目

先把合成资料的题目另存为 `questions-campus.jsonl`，再重新写一份 `questions.jsonl`，至少 20 道题：

- 15 道有答案的题。**用平时说话的方式来问**，不要照抄资料原文。直接用原文提问，检索结果会好得失真。
- 5 道和学校相关、但资料里没有答案的题。

### 运行和分析

```powershell
.\.venv\Scripts\python.exe -m backend.ingest
.\.venv\Scripts\python.exe -m backend.scripts.evaluate
```

逐条看每一行标着 `XX` 的结果，把原因归到下面几类里，并在 PyCharm 里打开对应的片段核实：

| 类型 | 表现 |
|---|---|
| 切坏了 | 答案被切到了两段中间 |
| 用词对不上 | 问的是"补卡"，资料里写的是"补办"，结果分数偏低 |
| 片段太杂 | 答案所在的那段里混了好几个话题，向量被"平均"掉了 |
| 需要多段 | 答案要把两份资料合起来才能得出 |
| 没有答案却通过了阈值 | 和 step7 里"宿舍熄灯"的情况一样 |

然后**一次只改一个参数**，重新跑评估，把结果记到表里：

| 改动 | 需要重新 ingest 吗？ | hit@1 | hit@3 | kept | rejected |
|---|---|---|---|---|---|
| 基线（600/100, MIN_SCORE=0.45） | — | | | | |
| CHUNK_SIZE=300 | ? | | | | |
| CHUNK_SIZE=1000 | ? | | | | |
| MIN_SCORE 调整为 ___ | ? | | | | |

**想一想**：哪些参数改了之后必须重新 ingest，哪些不用？为什么？

---

## 3. 端到端对比：数字人里的两种模式

检索评估只能说明"资料找没找对"，不能说明"数字人答没答对"。最后要在数字人里，用同一组问题，比较两种模式的效果。

### 准备

1. 从题库里挑 10 道题：5 道有答案的，2 道追问（比如"那周末呢"），3 道资料里没有答案的。
2. 加两行临时日志，用来看每轮发出去多少字：
   - 在 `BuildInitialKnowledgeBaseSystemMessage` 里：`Debug.Log($"[KB] 全文注入 {fullContext.Length} 字");`
   - 在 `BuildRagContextAsync` 的日志里，加上 `{context.Length} 字`。

### 测试

| 模式 | 设置 | 操作 |
|---|---|---|
| A 全文注入 | 不勾选 `useRagRetrieval` | 新对话，在**同一个对话**里依次问完 10 道题 |
| B RAG | 勾选 `useRagRetrieval` | 新对话，在同一个对话里依次问完同样的 10 道题 |

每道题记一个结果：**✓ 答对**；**✗ 答错**；**编** 资料里没有，它却编了一个答案；**无** 正确地回答了"知识库里没有"。

| # | 问题 | A 结果 | B 结果 | B 检索到了正确资料吗？ |
|---|---|---|---|---|
| 1 | | | | |
| … | | | | |

再记录：A 模式每轮带了多少字的知识库（看 `[KB]` 日志）；B 模式每轮带了多少字（看 `[RAG]` 日志，取平均值）。

---

## 4. 怎么读结果

先不要预设"RAG 一定更好"。

- **资料不多的时候**（全部内容只有几千字），全文注入往往和 RAG 一样好，甚至更好：模型能看到全部资料，不存在"没检索到"的问题。RAG 每轮还会多出 0.3 秒左右的检索时间。
- **RAG 在这些情况下会占优**：资料多到塞不进上下文，或者每轮都带全部资料太贵、太慢；资料里有很多相似的内容，全塞给模型反而容易混淆；或者资料在会话进行中会更新。
- **如果 RAG 输了，这同样是一个有效的结论。** 关键是要说清楚**输在了哪里**：是检索没找对（看第 2 节的分析），还是找对了，但模型没用好（改提示词）？

---

## 5. 验收

- [ ] 全部测试通过（29 个）
- [ ] 真实资料至少 20 份，题目至少 20 道，检索评估的结果和参数对比表都已记录
- [ ] 两种模式的端到端对比表已填完
- [ ] 能用自己的话讲清楚：在我的资料上，RAG 比全文注入好或者差，原因是什么；下一步最值得改进的是什么

对照答案：[step10-answer.md](step10-answer.md)。
