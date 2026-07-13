"""Golden-set judge validation harness (Phase 4d).

Runs the LLM faithfulness judge over a small hand-labeled set and reports
judge<->human agreement: accuracy plus hallucination-detection precision/recall
(positive class = *unfaithful*). This is what makes the judge *defensible* — you
can state "the judge agrees with human labels X% of the time" rather than just
"I added a faithfulness check".

Run against the real judge (needs OPENAI_API_KEY):

    python -m backend.evaluation.golden
"""

import asyncio
import json
from pathlib import Path
from typing import Any, List, Optional

from backend.evaluation.judge import ajudge_email
from backend.models import AgreementReport, GoldenExample

_GOLDEN_PATH = Path(__file__).parent / "data" / "golden.json"


def load_golden(path: Optional[Path] = None) -> List[GoldenExample]:
    data = json.loads((path or _GOLDEN_PATH).read_text())
    return [GoldenExample(**item) for item in data]


async def run_agreement(
    examples: List[GoldenExample], judge_agent: Any, max_concurrency: int = 3
) -> AgreementReport:
    """Judge every example and compare to its human label."""
    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _one(ex: GoldenExample):
        async with sem:
            return await ajudge_email(
                ex.email.model_dump(), [i.model_dump() for i in ex.evidence], judge_agent
            )

    verdicts = await asyncio.gather(*(_one(ex) for ex in examples))

    tp = fp = tn = fn = errors = agreements = evaluated = 0
    for ex, v in zip(examples, verdicts):
        if v.error is not None or v.faithful is None:
            errors += 1
            continue
        evaluated += 1
        pred, actual = v.faithful, ex.label_faithful
        if pred == actual:
            agreements += 1
        pred_flag, actual_flag = (not pred), (not actual)  # positive = unfaithful
        if pred_flag and actual_flag:
            tp += 1
        elif pred_flag and not actual_flag:
            fp += 1
        elif not pred_flag and actual_flag:
            fn += 1
        else:
            tn += 1

    return AgreementReport(
        total=len(examples),
        evaluated=evaluated,
        errors=errors,
        agreements=agreements,
        accuracy=(agreements / evaluated) if evaluated else 0.0,
        true_pos=tp,
        false_pos=fp,
        true_neg=tn,
        false_neg=fn,
        precision=(tp / (tp + fp)) if (tp + fp) else 0.0,
        recall=(tp / (tp + fn)) if (tp + fn) else 0.0,
    )


def format_report(r: AgreementReport) -> str:
    return (
        f"Golden set: {r.total} examples ({r.evaluated} evaluated, {r.errors} judge errors)\n"
        f"Agreement (accuracy): {r.accuracy:.0%}  ({r.agreements}/{r.evaluated})\n"
        f"Hallucination detection (positive = unfaithful):\n"
        f"  precision: {r.precision:.0%}   recall: {r.recall:.0%}\n"
        f"  TP={r.true_pos}  FP={r.false_pos}  TN={r.true_neg}  FN={r.false_neg}"
    )


def main() -> None:
    from backend.config import export_provider_env, get_settings
    from backend.evaluation.judge import create_judge_agent

    settings = get_settings()
    try:
        settings.require_openai_key()  # judge is OpenAI-only; Exa not needed here
    except Exception as e:
        print(f"Cannot run golden eval: {e}")
        return
    export_provider_env(settings)

    examples = load_golden()
    report = asyncio.run(run_agreement(examples, create_judge_agent()))
    print(format_report(report))


if __name__ == "__main__":
    main()
