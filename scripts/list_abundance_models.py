import json
import inspect
import re
import sys

from pysme.abund import Abund


def extract_models():
    source = inspect.getsource(Abund.set_pattern_by_name)
    models = []

    def add_model(name):
        if name and name not in models:
            models.append(name)

    for match in re.finditer(r"pattern_name\.lower\(\)\s*in\s*\[([^\]]+)\]", source):
        block = match.group(1)
        for item in re.findall(r"['\"]([^'\"]+)['\"]", block):
            add_model(item)

    for match in re.finditer(r"pattern_name\.lower\(\)\s*==\s*['\"]([^'\"]+)['\"]", source):
        add_model(match.group(1))

    return models


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: list_abundance_models.py <output_json>")
    output_path = sys.argv[1]
    models = extract_models()
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump({"models": models}, fh, indent=2)


if __name__ == "__main__":
    main()
