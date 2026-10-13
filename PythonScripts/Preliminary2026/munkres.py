"""小型、无第三方依赖的 Kuhn-Munkres（匈牙利）最小代价匹配实现。"""

from math import inf


class Munkres:
    """求最小总代价的一对一匹配（行数不得多于列数）。"""

    def compute(self, costs):
        """返回最小总代价匹配的 ``[(row, column), ...]``。

        使用 Hungarian / Kuhn-Munkres 的势能与增广路形式，时间复杂度 O(n²m)。
        本项目实际调用为 2×2 矩阵；这里仍检查输入，防止非法数值悄悄变成控制命令。
        """
        if not costs:
            return []
        row_count = len(costs)
        column_count = len(costs[0])
        if column_count == 0 or row_count > column_count:
            raise ValueError("Munkres 代价矩阵必须满足 1 <= 行数 <= 列数")
        if any(len(row) != column_count for row in costs):
            raise ValueError("Munkres 代价矩阵必须是矩形")
        if any(value != value or value in (inf, -inf) for row in costs for value in row):
            raise ValueError("Munkres 代价必须是有限数")

        u = [0.0] * (row_count + 1)
        v = [0.0] * (column_count + 1)
        p = [0] * (column_count + 1)
        way = [0] * (column_count + 1)
        for row in range(1, row_count + 1):
            p[0] = row
            column0 = 0
            minimum = [inf] * (column_count + 1)
            used = [False] * (column_count + 1)
            while True:
                used[column0] = True
                row0 = p[column0]
                delta = inf
                next_column = 0
                for column in range(1, column_count + 1):
                    if used[column]:
                        continue
                    reduced = costs[row0 - 1][column - 1] - u[row0] - v[column]
                    if reduced < minimum[column]:
                        minimum[column] = reduced
                        way[column] = column0
                    if minimum[column] < delta:
                        delta = minimum[column]
                        next_column = column
                for column in range(column_count + 1):
                    if used[column]:
                        u[p[column]] += delta
                        v[column] -= delta
                    else:
                        minimum[column] -= delta
                column0 = next_column
                if p[column0] == 0:
                    break
            while True:
                previous = way[column0]
                p[column0] = p[previous]
                column0 = previous
                if column0 == 0:
                    break
        return [(p[column] - 1, column - 1)
                for column in range(1, column_count + 1) if p[column] != 0]
