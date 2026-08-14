"""数据库索引原理示例。

B+树索引：所有键都在叶子节点，叶子节点按顺序链接，适合等值查询与范围查询。
哈希索引：将键映射到桶，等值查询 O(1)，但不支持范围查询，且哈希碰撞会退化。
倒排索引：记录「词 → 文档列表」的映射，是全文搜索引擎的核心结构。
聚簇索引：数据行按索引键物理排序，一个表只能有一个；非聚簇索引保存指向数据行的指针。
索引的写入代价：每次插入/更新/删除都要同步维护索引结构，索引过多会显著拖慢写入。
唯一索引：保证键的唯一性，插入重复键会报错。
前缀索引：只对键的前缀建索引，节省空间，适用于长字符串列，但会降低选择性。
"""


def btree_search(keys: list[int], target: int) -> bool:
    """B+树索引支持的等值查询：二分查找，O(log n)。"""
    lo, hi = 0, len(keys) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if keys[mid] == target:
            return True
        if keys[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return False


def range_query(keys: list[int], low: int, high: int) -> list[int]:
    """B+树索引支持的范围查询：叶子节点有序链接，顺序扫描区间即可。"""
    return [k for k in keys if low <= k <= high]


def build_inverted_index(docs: dict[str, str]) -> dict[str, set[str]]:
    """构建倒排索引：词 → 出现该词的文档集合。"""
    index: dict[str, set[str]] = {}
    for doc_id, text in docs.items():
        for word in text.split():
            index.setdefault(word, set()).add(doc_id)
    return index


def choose_index_column(selectivity: float) -> str:
    """索引选择：选择性高的列（值分布分散）适合建索引；选择性过低会导致索引失效。"""
    if selectivity > 0.1:
        return "适合建立索引"
    return "不适合建立索引，扫描全表可能更快"
