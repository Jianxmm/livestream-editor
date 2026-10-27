#!/usr/bin/env python3
"""Build a local, anonymized A/B review package for one interview."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from benchmark_pair import analyze_pair
from route_livestream import file_sha256
from freeze_input_bundle import MANIFEST as INPUT_BUNDLE_MANIFEST, verify_input_bundle


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reader_scorecard(reader_id: str) -> str:
    return f"""# 匿名读者评审表｜{reader_id}

请先按文件名前缀阅读两版，不查看任务根目录或作者信息。只记录真正让你停顿、回读、出戏或愿意继续读的具体位置。

- 评审状态：待填写
- 更像在听直播：A / B / 持平
- 更愿意读完：A / B / 持平
- 人物更鲜活：A / B / 持平
- 长但不拖沓：A / B / 持平
- 更适合直接发布：A / B / 持平
- 最终偏好：A / B / 持平

## 同尺度体验评分

两版都要独立打分，不得用偏好票替代分数。只评不需要查看原稿的三个维度。

| 维度 | 权重 | 版本A | 版本B |
| --- | ---: | ---: | ---: |
| 对谈现场与人物原声（20） | 20 | 待填 | 待填 |
| 阅读导航与节奏（10） | 10 | 待填 | 待填 |
| 排版与运营完成度（5） | 5 | 待填 | 待填 |

| 可定位缺陷 | 版本A（处数＋位置） | 版本B（处数＋位置） |
| --- | --- | --- |
| 问答逻辑不成立或疑似串台 |  |  |
| 同一说话人重复署名或切分过碎 |  |  |
| 口头禅、病句或ASR残留影响阅读 |  |  |
| 改得过于书面，不像本人说话 |  |  |
| 关键场景、追问或限定条件缺失 |  |  |
| 内容重复、拖沓或章节位置不合理 |  |  |

- 版本A最打动人的三处：
- 版本B最打动人的三处：
- 版本A最需要修改的三处：
- 版本B最需要修改的三处：
- 关键理由：
- 评审人代号：
- 评审日期：
"""


def source_scorecard(metrics: dict[str, dict[str, object]]) -> str:
    lines = [
        "# 来源评审表",
        "",
        "来源评审人阅读同一份原稿并按时间码抽听，只评事实、归属、遗漏、风险和可追溯性；不要查看作者映射。",
        "",
        "- 评审状态：待填写",
        "- 输入一致性：待确认",
        "- 来源评审人：待填",
        "- 评审日期：待填",
        "",
        "## 机器预检（不代替人工结论）",
        "",
        "| 指标 | 版本A | 版本B |",
        "| --- | ---: | ---: |",
    ]
    for label, key, formatter in (
        ("署名发言块", "published_turns", lambda value: str(value)),
        ("纯问答字符", "published_interview_chars", lambda value: str(value)),
        ("纯问答保留率", "retention_ratio", lambda value: f"{float(value):.2%}"),
    ):
        lines.append(
            f"| {label} | {formatter(metrics['A'][key])} | {formatter(metrics['B'][key])} |"
        )
    lines.extend(
        [
            "",
            "## 发布阻断与评分",
            "",
            "| 检查 | 版本A | 版本B | 时间码/证据 |",
            "| --- | --- | --- | --- |",
            "| 编造事实 | 0/有 | 0/有 |  |",
            "| 说话人归属错误 | 0/有 | 0/有 |  |",
            "| A级内容静默遗漏 | 0/有 | 0/有 |  |",
            "| 未标记低置信度内容 | 0/有 | 0/有 |  |",
            "| 已确认专名错写 | 0/有 | 0/有 |  |",
            "| 忠实与可追溯（35） |  |  |  |",
            "| 原始内容完整度（30） |  |  |  |",
            "",
            "- 是否允许进入读者体验总分比较：待确认",
            "- 关键事实与遗漏结论：",
            "- 两版都存在的问题：",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_review_package(
    case_dir: Path,
    first_draft: Path,
    second_draft: Path,
    output_dir: Path,
    *,
    readers: int = 4,
    experiment_type: str = "双轨对比",
    reference_exposure: str = "未在生成阶段使用对照稿",
    campaign_manifest: Path | None = None,
) -> dict[str, object]:
    if readers < 3:
        raise ValueError("匿名读者至少需要3人")
    if experiment_type not in {"双轨对比", "已知标杆回归"}:
        raise ValueError("实验类型只能是“双轨对比”或“已知标杆回归”。")
    if reference_exposure not in {"未在生成阶段使用对照稿", "生成阶段已见对照稿"}:
        raise ValueError("参考稿暴露状态无效。")
    if experiment_type == "双轨对比" and reference_exposure != "未在生成阶段使用对照稿":
        raise ValueError("真正双轨对比不能在生成阶段使用对照稿。")
    source_path = case_dir / "inputs/02_带时间码原稿.md"
    if not source_path.is_file():
        raise FileNotFoundError(f"缺少带时间码原稿：{source_path}")
    for draft in (first_draft, second_draft):
        if not draft.is_file():
            raise FileNotFoundError(f"缺少对比稿：{draft}")
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，避免覆盖评审结果：{output_dir}")

    input_bundle: dict[str, object] | None = None
    if experiment_type == "双轨对比":
        input_bundle = verify_input_bundle(case_dir)
    elif (case_dir / INPUT_BUNDLE_MANIFEST).is_file():
        input_bundle = verify_input_bundle(case_dir)

    source = source_path.read_text(encoding="utf-8-sig")
    source_hash = file_sha256(source_path)
    campaign_manifest_hash: str | None = None
    campaign_name: str | None = None
    if experiment_type == "双轨对比":
        if campaign_manifest is None or not campaign_manifest.is_file():
            raise FileNotFoundError("真正双轨对比必须先提供验证批次预注册manifest。")
        campaign_text = campaign_manifest.read_text(encoding="utf-8-sig")
        campaign = json.loads(campaign_text)
        planned = campaign.get("samples")
        if not isinstance(planned, list) or not any(
            isinstance(sample, dict)
            and sample.get("case") == case_dir.name
            and sample.get("source_sha256") == source_hash
            and sample.get("input_bundle_sha256") == input_bundle["bundle_sha256"]
            for sample in planned
        ):
            raise ValueError("当前场次、原稿或输入包不在该预注册批次中。")
        campaign_manifest_hash = sha256_text(campaign_text)
        campaign_name = str(campaign.get("campaign_name", ""))
    versions = {
        "first": first_draft.read_text(encoding="utf-8-sig"),
        "second": second_draft.read_text(encoding="utf-8-sig"),
    }
    hashes = {"first": file_sha256(first_draft), "second": file_sha256(second_draft)}
    if sha256_text(versions["first"]) == sha256_text(versions["second"]):
        raise ValueError("两份对比稿内容完全相同，不能建立双轨评审")

    seed = hashlib.sha256(
        (sha256_text(source) + hashes["first"] + hashes["second"]).encode("ascii")
    ).digest()[0]
    mapping = (
        {"A": "first", "B": "second"}
        if seed % 2 == 0
        else {"A": "second", "B": "first"}
    )
    metrics = {
        label: analyze_pair(
            source_path,
            first_draft if version == "first" else second_draft,
        )
        for label, version in mapping.items()
    }

    output_dir.mkdir(parents=True)
    source_dir = output_dir / "来源评审"
    source_dir.mkdir()
    for label in ("A", "B"):
        version = mapping[label]
        (source_dir / f"版本{label}.md").write_text(versions[version], encoding="utf-8")
    (source_dir / "来源评审表.md").write_text(
        source_scorecard(metrics), encoding="utf-8"
    )

    reader_orders: list[dict[str, object]] = []
    for index in range(1, readers + 1):
        reader_id = f"R{index:02d}"
        order = ["A", "B"] if index % 2 else ["B", "A"]
        reader_dir = output_dir / reader_id
        reader_dir.mkdir()
        for position, label in enumerate(order, start=1):
            version = mapping[label]
            (reader_dir / f"0{position}_版本{label}.md").write_text(
                versions[version], encoding="utf-8"
            )
        (reader_dir / "03_匿名评审表.md").write_text(
            reader_scorecard(reader_id), encoding="utf-8"
        )
        reader_orders.append({"reader": reader_id, "order": order})

    manifest: dict[str, object] = {
        "version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "case": case_dir.name,
        "experiment_type": experiment_type,
        "reference_exposure": reference_exposure,
        "source": str(source_path.resolve()),
        "source_sha256": source_hash,
        "input_bundle_sha256": (
            input_bundle.get("bundle_sha256") if input_bundle else None
        ),
        "campaign_name": campaign_name,
        "campaign_manifest": (
            str(campaign_manifest.resolve()) if campaign_manifest_hash else None
        ),
        "campaign_manifest_sha256": campaign_manifest_hash,
        "drafts": {
            "first": {"path": str(first_draft.resolve()), "sha256": hashes["first"]},
            "second": {"path": str(second_draft.resolve()), "sha256": hashes["second"]},
        },
        "private_mapping": mapping,
        "track_roles": {"first": "AI主要稿", "second": "人工团队或对照稿"},
        "reader_orders": reader_orders,
        "instructions": "只把单个Rxx目录交给对应读者；评审完成前不公开private_mapping。",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    comparison_template = (
        Path(__file__).resolve().parents[1]
        / "assets/templates/06_双轨质量对比表.md"
    )
    if comparison_template.is_file():
        (output_dir / "双轨质量对比表.md").write_text(
            comparison_template.read_text(encoding="utf-8-sig"), encoding="utf-8"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="生成同输入双轨匿名评审包。")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("comparison_draft", type=Path, help="人工团队或另一条独立流程的成稿")
    parser.add_argument(
        "--primary-draft",
        type=Path,
        help="主要AI稿；默认使用场次 outputs/01_公众号可发布稿.md",
    )
    parser.add_argument("--output-dir", type=Path, help="默认写入场次 _work/双轨匿名评审包")
    parser.add_argument("--readers", type=int, default=4)
    parser.add_argument("--campaign-manifest", type=Path)
    parser.add_argument(
        "--experiment-type",
        choices=("双轨对比", "已知标杆回归"),
        required=True,
    )
    parser.add_argument(
        "--reference-exposure",
        choices=("未在生成阶段使用对照稿", "生成阶段已见对照稿"),
        required=True,
    )
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    primary = (
        args.primary_draft.resolve()
        if args.primary_draft
        else case_dir / "outputs/01_公众号可发布稿.md"
    )
    output = (
        args.output_dir.resolve()
        if args.output_dir
        else case_dir / "_work/双轨匿名评审包"
    )
    manifest = prepare_review_package(
        case_dir,
        primary,
        args.comparison_draft.resolve(),
        output,
        readers=args.readers,
        experiment_type=args.experiment_type,
        reference_exposure=args.reference_exposure,
        campaign_manifest=args.campaign_manifest,
    )
    print(
        f"DUAL_TRACK=PASS readers={len(manifest['reader_orders'])} "
        f"destination={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
