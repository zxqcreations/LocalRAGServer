"""图算法实现与说明。

邻接表：对每个顶点保存其邻居列表，空间复杂度 O(V+E)，适合稀疏图。
BFS（广度优先搜索）：按层遍历，用于无权图最短路径。
DFS（深度优先搜索）：沿一条路径走到头再回溯，用于检测环、拓扑排序等。
Dijkstra：单源最短路径，每次扩展当前距离最小的顶点，不能处理负权边。
拓扑排序：对有向无环图（DAG）的线性排序，存在环时无法进行拓扑排序。
最小生成树：连接所有顶点的最小代价树，常见算法有 Kruskal 与 Prim。
强连通分量：有向图中任意两点互相可达的极大子图，可用 Tarjan 或 Kosaraju 算法求解。
检测环：有向图可用 DFS 三色标记法检测环，无向图可用并查集。
图的遍历复杂度：邻接表表示下 BFS/DFS 均为 O(V+E)。
"""
from collections import deque


def bfs(graph: dict[int, list[int]], start: int) -> list[int]:
    """广度优先搜索：返回按层序访问的顶点顺序。"""
    visited = {start}
    order: list[int] = []
    queue: deque[int] = deque([start])
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return order


def dijkstra(graph: dict[int, list[tuple[int, int]]], start: int) -> dict[int, int]:
    """Dijkstra 单源最短路径：返回 start 到各顶点的最短距离。

    注：图必须无负权边——负权边会破坏「当前最小距离即最终距离」的贪心前提。
    """
    import heapq

    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, float("inf")):
            continue
        for neighbor, weight in graph.get(node, []):
            nd = d + weight
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                heapq.heappush(heap, (nd, neighbor))
    return dist


def topological_sort(graph: dict[int, list[int]]) -> list[int]:
    """拓扑排序（Kahn 算法）：对 DAG 返回合法顺序；存在环时抛出异常。"""
    indegree = {node: 0 for node in graph}
    for neighbors in graph.values():
        for n in neighbors:
            indegree[n] = indegree.get(n, 0) + 1
    queue = deque(node for node, deg in indegree.items() if deg == 0)
    order: list[int] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)
    if len(order) != len(graph):
        raise ValueError("图中存在环，无法进行拓扑排序")
    return order
