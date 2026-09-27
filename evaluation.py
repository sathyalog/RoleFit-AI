#!/usr/bin/env python3
"""Offline evaluation harness for the resume/JD screening logic in core/screening.py.

Two independent things are measured against a hand-labeled gold dataset:
  1. Extraction quality - precision/recall/F1 of the LLM's required_skills /
     candidate_skills vs. gold-labeled skill lists (needs a real LLM call; costs money).
  2. Decision accuracy    - agreement between CheckCriteria's ShortList/Reject
     output and the gold-labeled expected_decision (free in "logic" mode).

Modes:
  --mode logic (default): feeds GOLD skills/experience directly into
      compute_skill_match + CheckCriteria. Zero API calls, zero cost, fully
      deterministic. This is a self-check of the app's scoring/threshold math,
      NOT of LLM extraction quality (since inputs ARE the gold skills, skill
      P/R/F1 is trivially 1.0 here by construction).
  --mode e2e: for each gold case, calls the REAL extract_screening_output()
      (configured LLM, see core/llm.py) on gold resume_text/job_description, then scores the
      LLM's extracted skills against gold labels, and runs the real
      CheckCriteria on the LLM-derived numbers. Requires GROQ_API_KEY or ANTHROPIC_API_KEY.
  --mode all: runs both and prints both reports.

  --self-test: runs a built-in rigged case with a deliberately WRONG
      expected_decision and asserts the harness reports it as a MISMATCH -
      proof the scorer can actually fail, not just always pass. No dataset
      or API key needed. Ignores --mode.

  --add-case: turns a real resume+JD you tested in the app into a new gold
      case, appended to the dataset. YOU supply the gold labels (required
      skills, candidate skills, experience, expected decision) based on your
      own read of the resume/JD - the point is to grow the golden set from
      real uploads, not to let the LLM grade its own homework.

Usage:
  uv run python evaluation.py --self-test
  uv run python evaluation.py --mode logic
  uv run python evaluation.py --mode e2e
  uv run python evaluation.py --mode all --json-out eval/last_run.json
  uv run python evaluation.py --add-case --case-id my_real_upload_01 \\
      --resume-pdf ~/Downloads/my_resume.pdf --jd-file jd.txt \\
      --required-skills "Python,AWS,Docker" --candidate-skills "Python,AWS,Docker,Terraform" \\
      --candidate-experience 6 --experience-required 5 --expected-decision ShortList
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import List, Literal, Optional, Tuple

from dotenv import load_dotenv
from pypdf import PdfReader

from core.screening import CheckCriteria, build_structured_model, compute_skill_match, extract_screening_output

DEFAULT_DATASET_PATH = "eval/gold_dataset.json"
LIVE_RUNS_PATH = "eval/live_runs.json"


@dataclass
class GoldCase:
    id: str
    description: str
    resume_text: str
    job_description: str
    gold_required_skills: List[str]
    gold_candidate_skills: List[str]
    gold_candidate_experience: float
    gold_experience_required: float
    expected_decision: Literal["ShortList", "Reject"]
    notes: str = ""


@dataclass
class ExtractionScore:
    precision: float
    recall: float
    f1: float
    true_positive: int
    false_positive: int
    false_negative: int
    missing: List[str] = field(default_factory=list)
    extra: List[str] = field(default_factory=list)


@dataclass
class CaseResult:
    case_id: str
    mode: str
    predicted_decision: Optional[str]
    expected_decision: str
    decision_correct: bool
    predicted_skill_match: Optional[float]
    required_skills_score: Optional[ExtractionScore] = None
    candidate_skills_score: Optional[ExtractionScore] = None
    error: Optional[str] = None


def load_gold_dataset(path: str) -> List[GoldCase]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [GoldCase(**case) for case in raw["cases"]]


def normalize_skill_set(skills: List[str]) -> set[str]:
    return {s.strip().lower() for s in (skills or []) if s.strip()}


def score_extraction(predicted: List[str], gold: List[str]) -> ExtractionScore:
    pred_set = normalize_skill_set(predicted)
    gold_set = normalize_skill_set(gold)

    true_positive = len(pred_set & gold_set)
    false_positive = len(pred_set - gold_set)
    false_negative = len(gold_set - pred_set)

    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return ExtractionScore(
        precision=precision,
        recall=recall,
        f1=f1,
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        missing=sorted(gold_set - pred_set),
        extra=sorted(pred_set - gold_set),
    )


def run_logic_case(case: GoldCase) -> CaseResult:
    match = compute_skill_match(case.gold_required_skills, case.gold_candidate_skills)
    predicted_decision = CheckCriteria({
        "skill_match": match["skill_match"],
        "candidate_experience": case.gold_candidate_experience,
        "experience_required": case.gold_experience_required,
    })

    return CaseResult(
        case_id=case.id,
        mode="logic",
        predicted_decision=predicted_decision,
        expected_decision=case.expected_decision,
        decision_correct=predicted_decision == case.expected_decision,
        predicted_skill_match=match["skill_match"],
        required_skills_score=score_extraction(case.gold_required_skills, case.gold_required_skills),
        candidate_skills_score=score_extraction(case.gold_candidate_skills, case.gold_candidate_skills),
    )


def run_e2e_case(case: GoldCase, structured_model) -> CaseResult:
    try:
        output = extract_screening_output(case.resume_text, case.job_description, structured_model)
    except Exception as exc:  # network/API/validation errors surface per-case, not fatal
        return CaseResult(
            case_id=case.id,
            mode="e2e",
            predicted_decision=None,
            expected_decision=case.expected_decision,
            decision_correct=False,
            predicted_skill_match=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    req_skills = output.required_skills or []
    cand_skills = output.candidate_skills or []
    match = compute_skill_match(req_skills, cand_skills)
    predicted_decision = CheckCriteria({
        "skill_match": match["skill_match"],
        "candidate_experience": output.candidate_experience,
        "experience_required": output.experience_required,
    })

    return CaseResult(
        case_id=case.id,
        mode="e2e",
        predicted_decision=predicted_decision,
        expected_decision=case.expected_decision,
        decision_correct=predicted_decision == case.expected_decision,
        predicted_skill_match=match["skill_match"],
        required_skills_score=score_extraction(req_skills, case.gold_required_skills),
        candidate_skills_score=score_extraction(cand_skills, case.gold_candidate_skills),
    )


def evaluate_dataset(dataset_path: str, modes: List[str]) -> List[Tuple[str, List[CaseResult], dict]]:
    """Run one or more modes ('logic'/'e2e') against a gold dataset. Returns
    a list of (mode, results, summary) tuples - the shared shape used by the
    CLI report, the HTML report, and the Streamlit evaluation tab."""
    cases = load_gold_dataset(dataset_path)
    mode_reports: List[Tuple[str, List[CaseResult], dict]] = []
    for mode in modes:
        if mode == "logic":
            results = [run_logic_case(c) for c in cases]
        else:
            structured_model = build_structured_model()
            results = [run_e2e_case(c, structured_model) for c in cases]
        mode_reports.append((mode, results, summarize(results)))
    return mode_reports


def _macro_average(scores: List[ExtractionScore]) -> dict:
    if not scores:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    n = len(scores)
    return {
        "precision": sum(s.precision for s in scores) / n,
        "recall": sum(s.recall for s in scores) / n,
        "f1": sum(s.f1 for s in scores) / n,
    }


def summarize(results: List[CaseResult]) -> dict:
    scored = [r for r in results if r.error is None]
    total = len(results)
    correct = sum(1 for r in scored if r.decision_correct)

    confusion = {
        ("ShortList", "ShortList"): 0,
        ("ShortList", "Reject"): 0,
        ("Reject", "ShortList"): 0,
        ("Reject", "Reject"): 0,
    }
    for r in scored:
        confusion[(r.expected_decision, r.predicted_decision)] += 1

    return {
        "total_cases": total,
        "errored_cases": total - len(scored),
        "correct": correct,
        "accuracy": correct / len(scored) if scored else 0.0,
        "confusion_matrix": {f"exp_{a}_pred_{b}": v for (a, b), v in confusion.items()},
        "required_skills_macro": _macro_average([r.required_skills_score for r in scored if r.required_skills_score]),
        "candidate_skills_macro": _macro_average([r.candidate_skills_score for r in scored if r.candidate_skills_score]),
    }


def build_full_report(mode_reports: List[Tuple[str, List[CaseResult], dict]], dataset_path: str) -> dict:
    """The persisted shape of eval/last_run.json - includes per-mode summaries
    (accuracy, confusion matrix, macro F1), not just raw case results, so a
    UI can render metrics from disk without re-running anything."""
    return {
        "dataset": dataset_path,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "runs": [
            {"mode": mode, "summary": summary, "results": [asdict(r) for r in results]}
            for mode, results, summary in mode_reports
        ],
    }


def load_full_report(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def append_live_run(record: dict, path: str = LIVE_RUNS_PATH) -> None:
    """Log a real (unlabeled - no ground truth) resume/JD analysis run from
    the Streamlit app, so it can later be reviewed and promoted into the gold
    dataset via save_live_run_as_gold_case."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as f:
            runs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        runs = []
    runs.append(record)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2)


def load_live_runs(path: str = LIVE_RUNS_PATH) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def append_case_to_dataset(dataset_path: str, case: "GoldCase") -> Tuple[bool, str]:
    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"schema_version": 1, "cases": []}

    if any(c["id"] == case.id for c in data["cases"]):
        return False, f"A case with id '{case.id}' already exists in {dataset_path}. Choose a different id."

    data["cases"].append(asdict(case))
    os.makedirs(os.path.dirname(dataset_path) or ".", exist_ok=True)
    with open(dataset_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True, f"Added case '{case.id}' to {dataset_path} ({len(data['cases'])} cases total)."


def save_live_run_as_gold_case(
    run: dict,
    expected_decision: Literal["ShortList", "Reject"],
    notes: str = "",
    dataset_path: str = DEFAULT_DATASET_PATH,
    gold_required_skills: Optional[List[str]] = None,
    gold_candidate_skills: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Promote a logged live run into the gold dataset. By default reuses the
    app's own extracted skills as the gold labels (a human is expected to
    override these via gold_required_skills/gold_candidate_skills when the
    extraction itself looks wrong) - the human-supplied expected_decision is
    always independent of what the app predicted."""
    case = GoldCase(
        id=f"live_{run['run_id']}",
        description=f"Promoted from a live app run on {run['timestamp']} ({run.get('job_title', 'Unknown Role')} @ {run.get('company_name', 'Unknown Company')}).",
        resume_text=run.get("resume_text", ""),
        job_description=run.get("job_description", ""),
        gold_required_skills=gold_required_skills if gold_required_skills is not None else run.get("required_skills", []),
        gold_candidate_skills=gold_candidate_skills if gold_candidate_skills is not None else run.get("candidate_skills", []),
        gold_candidate_experience=run.get("candidate_experience") or 0.0,
        gold_experience_required=run.get("experience_required") or 0.0,
        expected_decision=expected_decision,
        notes=notes,
    )
    return append_case_to_dataset(dataset_path, case)


def print_report(results: List[CaseResult], summary: dict, mode: str, dataset_path: str, verbose: bool = False) -> None:
    print("=== Resume/JD Screening Evaluation ===")
    cost_note = "NO LLM calls, $0 cost" if mode == "logic" else "calls the real LLM"
    print(f"Dataset: {dataset_path} ({len(results)} cases)")
    print(f"Mode: {mode} ({cost_note})\n")

    header = f"{'Case ID':<38}{'Expected':<11}{'Predicted':<11}{'Match':<7}{'SkillMatch':<10}"
    print(header)
    for r in results:
        if r.error:
            print(f"{r.case_id:<38}{r.expected_decision:<11}{'ERROR':<11}{'--':<7}{'--':<10}  ({r.error})")
            continue
        match_flag = "OK" if r.decision_correct else "MISS"
        skill_match_str = f"{r.predicted_skill_match:.2f}" if r.predicted_skill_match is not None else "--"
        print(f"{r.case_id:<38}{r.expected_decision:<11}{r.predicted_decision:<11}{match_flag:<7}{skill_match_str:<10}")
        if verbose and r.required_skills_score:
            rs, cs = r.required_skills_score, r.candidate_skills_score
            print(f"    required_skills:  P={rs.precision:.2f} R={rs.recall:.2f} F1={rs.f1:.2f} missing={rs.missing} extra={rs.extra}")
            print(f"    candidate_skills: P={cs.precision:.2f} R={cs.recall:.2f} F1={cs.f1:.2f} missing={cs.missing} extra={cs.extra}")

    print("\n--- Decision Accuracy ---")
    print(f"Accuracy: {summary['correct']}/{summary['total_cases'] - summary['errored_cases']} "
          f"({summary['accuracy'] * 100:.1f}%)" + (f"  [{summary['errored_cases']} errored]" if summary["errored_cases"] else ""))
    cm = summary["confusion_matrix"]
    print("Confusion Matrix (rows=expected, cols=predicted):")
    print(f"{'':<20}{'Pred:ShortList':<18}{'Pred:Reject':<12}")
    print(f"{'Exp:ShortList':<20}{cm['exp_ShortList_pred_ShortList']:<18}{cm['exp_ShortList_pred_Reject']:<12}")
    print(f"{'Exp:Reject':<20}{cm['exp_Reject_pred_ShortList']:<18}{cm['exp_Reject_pred_Reject']:<12}")

    print("\n--- Skill Extraction Quality ---")
    if mode == "logic":
        print("NOTE: logic mode compares gold skills to themselves - trivially perfect.")
        print("Run --mode e2e to measure real LLM extraction quality against gold labels.")
    rs_macro = summary["required_skills_macro"]
    cs_macro = summary["candidate_skills_macro"]
    print(f"required_skills:  precision={rs_macro['precision']:.2f} recall={rs_macro['recall']:.2f} f1={rs_macro['f1']:.2f}")
    print(f"candidate_skills: precision={cs_macro['precision']:.2f} recall={cs_macro['recall']:.2f} f1={cs_macro['f1']:.2f}")


def render_html_report(mode_reports: List[tuple], dataset_path: str) -> str:
    """mode_reports: list of (mode, results, summary) tuples, one per mode run."""
    import html as html_lib

    def esc(s) -> str:
        return html_lib.escape(str(s))

    sections = []
    for mode, results, summary in mode_reports:
        cost_note = "NO LLM calls, $0 cost" if mode == "logic" else "calls the real LLM"
        rows = []
        for r in results:
            if r.error:
                rows.append(
                    f"<tr class='error'><td>{esc(r.case_id)}</td><td>{esc(r.expected_decision)}</td>"
                    f"<td colspan='2'>ERROR: {esc(r.error)}</td><td>--</td></tr>"
                )
                continue
            row_class = "ok" if r.decision_correct else "miss"
            match_label = "OK" if r.decision_correct else "MISMATCH"
            extra_rows = ""
            if r.required_skills_score:
                rs, cs = r.required_skills_score, r.candidate_skills_score
                extra_rows = (
                    f"<tr class='detail'><td colspan='5'>"
                    f"required_skills: P={rs.precision:.2f} R={rs.recall:.2f} F1={rs.f1:.2f}"
                    f" missing={esc(rs.missing)} extra={esc(rs.extra)}<br>"
                    f"candidate_skills: P={cs.precision:.2f} R={cs.recall:.2f} F1={cs.f1:.2f}"
                    f" missing={esc(cs.missing)} extra={esc(cs.extra)}"
                    f"</td></tr>"
                )
            rows.append(
                f"<tr class='{row_class}'><td>{esc(r.case_id)}</td><td>{esc(r.expected_decision)}</td>"
                f"<td>{esc(r.predicted_decision)}</td><td>{esc(match_label)}</td>"
                f"<td>{r.predicted_skill_match:.2f}</td></tr>{extra_rows}"
            )

        cm = summary["confusion_matrix"]
        rs_macro, cs_macro = summary["required_skills_macro"], summary["candidate_skills_macro"]
        trivial_note = (
            "<p class='note'>NOTE: logic mode compares gold skills to themselves - trivially perfect. "
            "Run --mode e2e to measure real LLM extraction quality.</p>" if mode == "logic" else ""
        )

        sections.append(f"""
        <section>
          <h2>Mode: {esc(mode)}</h2>
          <p class="cost">{esc(cost_note)}</p>
          <table class="cases">
            <thead><tr><th>Case ID</th><th>Expected</th><th>Predicted</th><th>Match</th><th>Skill Match</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>

          <h3>Decision Accuracy</h3>
          <p><strong>{summary['correct']}/{summary['total_cases'] - summary['errored_cases']}</strong>
             ({summary['accuracy'] * 100:.1f}%) correct
             {f"&mdash; {summary['errored_cases']} errored" if summary['errored_cases'] else ""}</p>
          <table class="confusion">
            <thead><tr><th></th><th>Pred: ShortList</th><th>Pred: Reject</th></tr></thead>
            <tbody>
              <tr><th>Exp: ShortList</th><td>{cm['exp_ShortList_pred_ShortList']}</td><td>{cm['exp_ShortList_pred_Reject']}</td></tr>
              <tr><th>Exp: Reject</th><td>{cm['exp_Reject_pred_ShortList']}</td><td>{cm['exp_Reject_pred_Reject']}</td></tr>
            </tbody>
          </table>

          <h3>Skill Extraction Quality</h3>
          {trivial_note}
          <p>required_skills: precision={rs_macro['precision']:.2f} recall={rs_macro['recall']:.2f} f1={rs_macro['f1']:.2f}</p>
          <p>candidate_skills: precision={cs_macro['precision']:.2f} recall={cs_macro['recall']:.2f} f1={cs_macro['f1']:.2f}</p>
        </section>
        """)

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Resume/JD Screening Evaluation Report</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; background: Canvas; color: CanvasText; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ margin-top: 2.5rem; border-bottom: 2px solid #888; padding-bottom: 0.3rem; }}
  h3 {{ margin-top: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.9rem; }}
  th, td {{ border: 1px solid #999; padding: 0.4rem 0.6rem; text-align: left; }}
  tr.ok td {{ background: rgba(46, 125, 50, 0.15); }}
  tr.miss td {{ background: rgba(198, 40, 40, 0.18); font-weight: 600; }}
  tr.error td {{ background: rgba(255, 152, 0, 0.18); }}
  tr.detail td {{ font-size: 0.8rem; opacity: 0.85; background: none; border-top: none; }}
  .meta {{ opacity: 0.75; font-size: 0.9rem; }}
  .note {{ font-style: italic; opacity: 0.8; }}
  .cost {{ font-weight: 600; }}
</style>
</head>
<body>
  <h1>Resume/JD Screening Evaluation Report</h1>
  <p class="meta">Dataset: {esc(dataset_path)} &middot; Generated: {esc(generated_at)}</p>
  {''.join(sections)}
</body>
</html>
"""


def add_gold_case(args: argparse.Namespace) -> int:
    """Append a new hand-labeled case (typically a real resume+JD you just
    tested in the Streamlit app) to the gold dataset. All gold_* labels come
    from the caller's own judgment, not from running the pipeline - the point
    is an independent ground truth to check the pipeline against."""
    missing = [
        name for name, val in [
            ("--case-id", args.case_id),
            ("--required-skills", args.required_skills),
            ("--candidate-skills", args.candidate_skills),
            ("--candidate-experience", args.candidate_experience),
            ("--experience-required", args.experience_required),
            ("--expected-decision", args.expected_decision),
        ] if val is None
    ]
    if missing:
        print(f"ERROR: --add-case requires: {', '.join(missing)}")
        return 1

    if args.resume_pdf:
        reader = PdfReader(args.resume_pdf)
        resume_text = "".join(page.extract_text() or "" for page in reader.pages)
    elif args.resume_text:
        resume_text = args.resume_text
    else:
        print("ERROR: --add-case requires either --resume-pdf or --resume-text.")
        return 1

    if args.jd_file:
        with open(args.jd_file, "r", encoding="utf-8") as f:
            job_description = f.read()
    elif args.jd_text:
        job_description = args.jd_text
    else:
        print("ERROR: --add-case requires either --jd-file or --jd-text.")
        return 1

    new_case = GoldCase(
        id=args.case_id,
        description=args.description or f"Real upload added via --add-case: {args.case_id}",
        resume_text=resume_text,
        job_description=job_description,
        gold_required_skills=[s.strip() for s in args.required_skills.split(",") if s.strip()],
        gold_candidate_skills=[s.strip() for s in args.candidate_skills.split(",") if s.strip()],
        gold_candidate_experience=args.candidate_experience,
        gold_experience_required=args.experience_required,
        expected_decision=args.expected_decision,
        notes=args.notes or "",
    )
    ok, msg = append_case_to_dataset(args.dataset, new_case)
    print(("ERROR: " if not ok else "") + msg)
    if ok:
        print("Run 'uv run python evaluation.py --mode e2e' to see how the real pipeline scores it.")
    return 0 if ok else 1


def self_test() -> bool:
    """Rig a case where CheckCriteria must return 'Reject', deliberately compare
    it against a wrong 'ShortList' expectation, and assert the harness reports
    a mismatch. Proves the scorer can actually fail, not just always pass."""
    rigged_state = {"skill_match": 0.2, "candidate_experience": 1.0, "experience_required": 5.0}
    predicted = CheckCriteria(rigged_state)
    wrong_expected = "ShortList"

    if predicted != "Reject":
        print(f"SELF-TEST FAILED: expected CheckCriteria to return 'Reject' for {rigged_state}, got '{predicted}'.")
        return False

    decision_correct = predicted == wrong_expected
    if decision_correct:
        print("SELF-TEST FAILED: harness reported a match for a deliberately wrong expectation - the scorer is broken.")
        return False

    print(f"SELF-TEST PASSED: harness correctly flagged a rigged mismatched case "
          f"(predicted={predicted}, mislabeled-expected={wrong_expected}).")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate resume/JD screening accuracy against a gold dataset.")
    parser.add_argument("--mode", choices=["logic", "e2e", "all"], default="logic")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--html-out", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--self-test", action="store_true")

    parser.add_argument("--add-case", action="store_true", help="Add a new hand-labeled case to the gold dataset instead of running an evaluation.")
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--resume-pdf", default=None)
    parser.add_argument("--resume-text", default=None)
    parser.add_argument("--jd-file", default=None)
    parser.add_argument("--jd-text", default=None)
    parser.add_argument("--required-skills", default=None, help="Comma-separated, e.g. 'Python,AWS,Docker'")
    parser.add_argument("--candidate-skills", default=None, help="Comma-separated, e.g. 'Python,AWS,Docker,Terraform'")
    parser.add_argument("--candidate-experience", type=float, default=None)
    parser.add_argument("--experience-required", type=float, default=None)
    parser.add_argument("--expected-decision", choices=["ShortList", "Reject"], default=None)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    if args.self_test:
        return 0 if self_test() else 1

    if args.add_case:
        return add_gold_case(args)

    load_dotenv(override=True)
    modes_to_run = ["logic", "e2e"] if args.mode == "all" else [args.mode]
    mode_reports = evaluate_dataset(args.dataset, modes_to_run)

    any_error = False
    for mode, results, summary in mode_reports:
        print_report(results, summary, mode, args.dataset, verbose=args.verbose)
        print()
        any_error = any_error or any(r.error for r in results) or summary["correct"] != (summary["total_cases"] - summary["errored_cases"])

    if args.json_out:
        full_report = build_full_report(mode_reports, args.dataset)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(full_report, f, indent=2)
        print(f"Wrote full report ({sum(len(r) for _, r, _ in mode_reports)} case results) to {args.json_out}")

    if args.html_out:
        html_report = render_html_report(mode_reports, args.dataset)
        with open(args.html_out, "w", encoding="utf-8") as f:
            f.write(html_report)
        print(f"Wrote HTML report to {args.html_out} (open it in a browser to view)")

    return 0 if not any_error else 1


if __name__ == "__main__":
    sys.exit(main())
