"""Hierarchy helpers shared by canvas rendering and portable exports."""
from copy import deepcopy


def descendants(group_id, groups):
    by_id = {str(row.get("id")): row for row in groups if isinstance(row, dict) and row.get("id")}
    found = set()
    pending = list(by_id.get(str(group_id), {}).get("group_nodes") or [])
    while pending:
        member = str(pending.pop())
        if member == str(group_id) or member in found:
            continue
        found.add(member)
        pending.extend(by_id.get(member, {}).get("group_nodes") or [])
    return found


def normalize_groups(groups, node_ids):
    """Keep a tree with one parent per member, dropping cycles and empty groups."""
    node_ids = set(node_ids)
    records = {}
    for row in groups:
        if isinstance(row, dict) and row.get("id") and str(row["id"]) not in node_ids:
            records.setdefault(str(row["id"]), deepcopy(row))
    parents = {}
    for group_id, row in records.items():
        members = []
        for value in row.get("group_nodes") or []:
            member = str(value)
            if member not in node_ids and member not in records:
                continue
            # Follow the proposed parent's chain to reject an ancestor as child.
            chain = group_id
            cyclic = member == chain
            while not cyclic and chain in parents:
                chain = parents[chain]
                cyclic = chain == member
            if cyclic or member in parents:
                continue
            parents[member] = group_id
            members.append(member)
        row["group_nodes"] = members
        row["id"] = group_id
        row["canvas_group"] = True
    changed = True
    while changed:
        empty = {key for key, row in records.items() if not row["group_nodes"]}
        changed = bool(empty)
        for key in empty:
            records.pop(key)
        for row in records.values():
            row["group_nodes"] = [value for value in row["group_nodes"] if value not in empty]
    return list(records.values())
