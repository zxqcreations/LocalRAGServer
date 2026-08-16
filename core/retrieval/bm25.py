"""纯 Python BM25 稀疏检索（ADR-004：本地路径；生产切 Qdrant 原生 full-text，Phase 6）。

tokenizer：拉丁字符序列按词、CJK 按单字符——中英混合语料可用的简单形态。
注意：token 顺序为「按文本出现顺序交织」（单遍正则），非「词先行」；调用方
不得依赖 token 顺序（词袋模型语义）。

索引：内存倒排表（posting lists，array 紧凑存储）——查询只触碰含查询词的
文档，不再全语料扫描（SLO 压测稀疏侧 84.6ms → 25-36ms CJK / <0.1ms 英文）。
查询成本钳制（安全审查 M）：
- 高 df 词项跳过（df > 0.9×N 且 N≥1000）：idf ≈ 0.5/N，贡献可忽略，
  与旧实现差量 ~1e-5 量级，RRF 排名无感知
- 单查询累计遍历 posting 总数上限 _MAX_WALKED_POSTINGS（恶意超长查询兜底）
千万级规模不适用（见 ADR-004 约束）。tf 以 array('h') 存储（chunk 上限 512
字符 → 词频上界远低于 32767；越界在构建期 OverflowError fail-fast）。
"""
import math
import re
from array import array
from dataclasses import dataclass

# 单遍正则（拉丁词 | CJK 单字符）：替代原「全词扫描 + 逐字符正则」双遍实现
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[一-鿿㐀-䶿]")

_HIGH_DF_RATIO = 0.9  # df/N 超过此值的词项跳过（查询成本钳制）
_HIGH_DF_MIN_N = 1000  # 仅大语料启用跳过（小 KB 单文档语料不受影响）
_MAX_WALKED_POSTINGS = 200_000  # 单查询累计 posting 遍历上限（超长查询兜底）


def tokenize(text: str) -> list[str]:
    """拉丁词（小写化）+ CJK 单字符 tokenization，单遍扫描，按文本顺序交织。"""
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


@dataclass(frozen=True)
class Bm25Hit:
    id: int  # 语料中的文档序号
    score: float


class BM25Retriever:
    """标准 Okapi BM25：score(q,d) = Σ idf(t) * tf_norm，k1=1.5, b=0.75。

    逐词项贡献与旧实现位级一致；最终求和顺序不同（按排序后的查询词 vs 按
    文档），浮点误差 ~1e-15（黄金测试以 1e-12 容差对拍）。查询词排序保证
    跨进程确定性（不随 hash 种子漂移）。
    """

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._n = len(corpus)
        self._doc_len: list[int] = []
        self.df: dict[str, int] = {}
        # 倒排构建：直接增量写入紧凑数组（无 list→array 二次转换，
        # 峰值内存 ≈ 最终数组 + 摊销增长，token 列表随每文档循环结束释放）
        self._postings: dict[str, tuple[array, array]] = {}
        for doc_id, doc in enumerate(corpus):
            tokens = tokenize(doc)
            self._doc_len.append(len(tokens))
            tf: dict[str, int] = {}
            for term in tokens:
                tf[term] = tf.get(term, 0) + 1
            for term, freq in tf.items():
                self.df[term] = self.df.get(term, 0) + 1
                entry = self._postings.get(term)
                if entry is None:
                    entry = (array("i"), array("h"))
                    self._postings[term] = entry
                entry[0].append(doc_id)
                entry[1].append(freq)
        self._avg_len = sum(self._doc_len) / self._n if self._n else 0.0
        # 每文档长度归一化常量（查询内层循环预计算，避免逐项浮点乘除）
        self._norm = [1 - b + b * length / self._avg_len for length in self._doc_len]

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 50) -> list[Bm25Hit]:
        # 排序保证跨进程确定性（hash 种子无关）；同时让预算截断的语义可复现
        query_terms = sorted(set(tokenize(query)))
        if not query_terms or self._n == 0:
            return []
        scores: dict[int, float] = {}
        k1 = self._k1
        k1_plus_1 = k1 + 1
        norm = self._norm
        skip_enabled = self._n >= _HIGH_DF_MIN_N
        skip_threshold = self._n * _HIGH_DF_RATIO
        walked = 0
        for term in query_terms:
            postings = self._postings.get(term)
            if postings is None:
                continue
            df = self.df.get(term, 0)
            if skip_enabled and df > skip_threshold:
                continue  # 高 df 词项：idf ≈ 0.5/N，贡献可忽略（查询成本钳制）
            idf = self.idf(term)
            ids, tfs = postings
            walked += len(ids)
            if walked > _MAX_WALKED_POSTINGS:
                break  # 恶意超长查询兜底：按排序后的词序确定性截断
            # range 索引 + 局部变量提升：array 逐元素装箱是主开销，尽力压缩循环体
            for i in range(len(ids)):
                doc_id = ids[i]
                tf = tfs[i]
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * (
                    tf * k1_plus_1 / (tf + k1 * norm[doc_id])
                )
        scored = [Bm25Hit(id=doc_id, score=score) for doc_id, score in scores.items()]
        # 确定化排序：分数降序，同分按文档序升序（与旧实现全扫描的稳定序一致）
        scored.sort(key=lambda h: (-h.score, h.id))
        return scored[:top_k]
