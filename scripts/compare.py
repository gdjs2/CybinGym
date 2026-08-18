#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from typing import Any


def load_records(file_path: Path) -> list[dict[str, Any]]:
    """
    Supports:
    1. JSON array:
       [
         {"task_id": "task_1"},
         {"task_id": "task_2"}
       ]

    2. JSON object containing a list under common keys such as
       "results", "records", "data", or "tasks".

    3. JSONL:
       {"task_id": "task_1"}
       {"task_id": "task_2"}
    """
    text = file_path.read_text(encoding="utf-8").strip()

    if not text:
        return []

    try:
        data = json.loads(text)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in ("results", "records", "data", "tasks"):
                if isinstance(data.get(key), list):
                    return data[key]

            # Treat a single object containing task_id as one record.
            if "task_id" in data:
                return [data]

        raise ValueError(
            f"Unsupported JSON structure in {file_path}. "
            "Expected a JSON array, JSONL, or an object containing a list."
        )

    except json.JSONDecodeError:
        records = []

        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {file_path}: {error}"
                ) from error

            if not isinstance(record, dict):
                raise ValueError(
                    f"Line {line_number} of {file_path} is not a JSON object."
                )

            records.append(record)

        return records


def extract_task_ids(
    records: list[dict[str, Any]],
    file_path: Path,
) -> set[str]:
    task_ids = set()

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            print(
                f"Warning: skipping non-object record at index {index} "
                f"in {file_path}"
            )
            continue

        task_id = record.get("id")

        if task_id is None:
            print(
                f"Warning: record at index {index} in {file_path} "
                "does not contain task_id"
            )
            continue

        task_ids.add(str(task_id))

    return task_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare task_id values between two JSON or JSONL files."
    )
    parser.add_argument("file1", type=Path, help="First JSON/JSONL file")
    parser.add_argument("file2", type=Path, help="Second JSON/JSONL file")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for saving the comparison result as JSON",
    )
    args = parser.parse_args()

    records1 = load_records(args.file1)
    records2 = load_records(args.file2)

    task_ids1 = extract_task_ids(records1, args.file1)
    task_ids2 = extract_task_ids(records2, args.file2)

    only_in_file1 = sorted(task_ids1 - task_ids2)
    only_in_file2 = sorted(task_ids2 - task_ids1)
    shared = task_ids1 & task_ids2

    result = {
        "file1": str(args.file1),
        "file2": str(args.file2),
        "file1_unique_task_ids": len(task_ids1),
        "file2_unique_task_ids": len(task_ids2),
        "shared_task_ids": len(shared),
        "only_in_file1": only_in_file1,
        "only_in_file2": only_in_file2,
    }

    print(f"\nUnique task IDs in {args.file1}: {len(task_ids1)}")
    print(f"Unique task IDs in {args.file2}: {len(task_ids2)}")
    print(f"Shared task IDs: {len(shared)}")

    print(f"\nTask IDs only in {args.file1} ({len(only_in_file1)}):")
    for task_id in only_in_file1:
        print(f"  {task_id}")

    print(f"\nTask IDs only in {args.file2} ({len(only_in_file2)}):")
    for task_id in only_in_file2:
        print(f"  {task_id}")

    if args.output:
        args.output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nResult saved to {args.output}")


if __name__ == "__main__":
    main()