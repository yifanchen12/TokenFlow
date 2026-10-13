"""Prepare manually verified real-task cases locally; never call a model."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval_workflow import load_cases, _validate_case


def prepare(manifest: Path, output: Path) -> int:
    if manifest.resolve() == output.resolve():
        raise ValueError("输出不能覆盖原始样本清单")
    cases = load_cases(manifest)
    for case in cases:
        if case.get("source_kind") != "real_task":
            raise ValueError("此工具只准备 source_kind=real_task 的真实任务样本")
        if not isinstance(case.get("category"), str) or not case["category"].strip():
            raise ValueError("请给样本填写 category（例如 long_document、table、ocr）")
        _validate_case(case, True)
        case.pop("candidate_answer", None)
        case.pop("source_file", None)  # Prepared cases are portable text snapshots.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, help="Create a blank real-task annotation manifest")
    parser.add_argument("--manifest", type=Path, help="Human-verified draft array; sources relative to this file")
    parser.add_argument("--output", type=Path, help="Prepared, portable JSON cases")
    args = parser.parse_args()
    try:
        if args.template:
            if args.manifest or args.output or args.template.exists():
                raise ValueError("模板路径须未存在，且不能同时准备样本")
            cases = [{"name": category, "category": category, "source_kind": "real_task", "label_status": "draft",
                      "task": "", "source_file": "", "must_keep": [], "expected_answer": "", "answer_match": "exact"}
                     for category in ("long_document", "table", "conflicting_information", "ocr")]
            args.template.parent.mkdir(parents=True, exist_ok=True)
            args.template.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Annotation template created. Fill actual tasks, sources, facts and independently checked answers; set label_status=verified.")
        elif args.manifest and args.output:
            print(f"Prepared {prepare(args.manifest, args.output)} manually verified real-task cases. No model calls.")
        else:
            parser.error("使用 --template，或同时使用 --manifest 和 --output")
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
