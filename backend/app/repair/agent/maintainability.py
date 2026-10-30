"""Reviewer prompts from source syntax; never a verdict or model-generated assurance."""
import ast
from app.repair.oracle import common as cm


def review_notes(base_dir: str, diff: str) -> list[str]:
    notes = ["Review the added regression test and confirm the fix shares the production feature-building path."]
    try:
        files = cm.apply_diff(base_dir, diff)
    except ValueError:
        return notes + ["Static maintainability inspection could not apply the patch; inspect the source diff manually."]
    lists: dict[tuple[str, ...], list[str]] = {}
    for path, raw in files.items():
        if raw is None or not path.endswith(".py") or len(raw) > 500_000:
            continue
        try:
            tree = ast.parse(raw)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) >= 3 and all(
                isinstance(x, ast.Constant) and isinstance(x.value, str) for x in node.elts
            ):
                lists.setdefault(tuple(x.value for x in node.elts), []).append(f"{path}:{node.lineno}")
            if isinstance(node, ast.ExceptHandler) and (node.type is None or isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}):
                notes.append(f"{path}:{node.lineno}: broad exception handling needs review; confirm failures remain observable.")
    for locations in lists.values():
        if len(locations) > 1:
            notes.append("Repeated literal column/list definition at " + ", ".join(locations[:4]) + "; consider one shared source of truth.")
    return list(dict.fromkeys(notes))[:10]
