"""Measure first, then arrange canvas cards and nested groups horizontally."""
import heapq


def plan_horizontal_layout(items, edges, origin, node_gap=80.0, group_gap=140.0):
    positions = {}

    def leaves(item):
        if "children" not in item:
            return {item["id"]}
        return set().union(*(leaves(child) for child in item["children"]))

    def ordered(values):
        by_id = {item["id"]: item for item in values}
        owners = {leaf: item["id"] for item in values for leaf in leaves(item)}
        outgoing = {key: set() for key in by_id}
        indegree = dict.fromkeys(by_id, 0)
        for source, target in edges:
            a, b = owners.get(source), owners.get(target)
            if a and b and a != b and b not in outgoing[a]:
                outgoing[a].add(b)
                indegree[b] += 1

        def key(item_id):
            item = by_id[item_id]
            return (*item.get("order", (0, 0)), item_id)

        ready = [key(item_id) for item_id in by_id if not indegree[item_id]]
        heapq.heapify(ready)
        result = []
        while ready:
            item_id = heapq.heappop(ready)[-1]
            result.append(by_id[item_id])
            for target in outgoing[item_id]:
                indegree[target] -= 1
                if not indegree[target]:
                    heapq.heappush(ready, key(target))
        seen = {item["id"] for item in result}
        result.extend(by_id[item_id] for item_id in sorted(set(by_id) - seen, key=key))
        return result

    def row(values, x, y):
        cursor = x
        height = 0.0
        previous = None
        for item in ordered(values):
            if previous is not None:
                cursor += group_gap if "children" in previous or "children" in item else node_gap
            width, item_height = place(item, cursor, y)
            cursor += width
            height = max(height, item_height)
            previous = item
        return cursor - x, height

    def place(item, x, y):
        if "children" not in item:
            offset_x, offset_y = item.get("offset", (0.0, 0.0))
            positions[item["id"]] = [round(x + offset_x, 2), round(y + offset_y, 2)]
            return item["width"], item["height"]
        width, height = row(item["children"], x + item["padding"], y + item["top_padding"])
        width = max(360.0, item.get("manual_width", 0), width + item["padding"] * 2)
        height = (46.0 if item.get("collapsed") else
                  max(150.0, item.get("manual_height", 0), height + item["top_padding"] + item["bottom_padding"]))
        return width, height

    row(items, float(origin[0]), float(origin[1]))
    return positions
