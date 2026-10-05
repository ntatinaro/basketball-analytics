"""Plain-text summary of a rolling exam run."""

from __future__ import annotations


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _market_lines(bench: dict, target: dict | None) -> list[str]:
    market, model = bench["market"], bench["model_on_market_games"]
    if market is None or target is None:
        return ["  market: no betting lines for this season"]
    return [
        f"  market ({market['games']} games): log loss {market['log_loss']:.4f}"
        f"  accuracy {_pct(market['accuracy'])}; model on same games:"
        f" {model['log_loss']:.4f}  {_pct(model['accuracy'])}",
        f"  target (within 1.5 pts accuracy and 0.01 log loss of market):"
        f" {'MET' if target['within_target'] else 'NOT MET'}"
        f" (accuracy gap {100 * target['accuracy_gap']:+.1f} pts,"
        f" log loss gap {target['log_loss_gap']:+.4f})",
    ]


def _floor_lines(bench: dict) -> list[str]:
    floor = bench.get("without_absences")
    if floor is None:
        return []
    line = (f"  honest floor (absences ignored; backtests otherwise know who sat out): log loss"
            f" {floor['log_loss']:.4f}  accuracy {_pct(floor['accuracy'])}")
    target = bench.get("without_absences_target")
    if target:
        line += (f"; vs market: accuracy gap {100 * target['accuracy_gap']:+.1f} pts,"
                 f" log loss gap {target['log_loss_gap']:+.4f}")
    return [line]


def format_exam_report(report: dict) -> str:
    lines = [f"Rolling exams: {report['league']} ({report['candidates']} candidates)", ""]
    for r in report["rounds"]:
        exam = r["exam"]
        bench = exam["benchmarks"]
        target = exam["target"]
        lines += [
            f"Round {r['round']}: tuned on {r['tuning_seasons']}, examined on {r['exam_season']}",
            f"  winner: {r['winner']}{' (blend)' if r['blend'] else ''}",
            f"  tuning log loss {r['tuning']['log_loss']:.4f}",
            f"  exam:   log loss {exam['log_loss']:.4f}  accuracy {_pct(exam['accuracy'])}"
            f"  margin error {exam['margin_mae']:.1f}  total error {exam['total_mae']:.1f}"
            f"  ({exam['games']} games)",
            f"  naive baseline: log loss {bench['naive']['log_loss']:.4f}"
            f"  accuracy {_pct(bench['naive']['accuracy'])}",
            f"  without roster start: log loss {bench['without_roster_start']['log_loss']:.4f}",
            *_market_lines(bench, target),
            *_floor_lines(bench),
            "",
        ]
    champ = report["champion"]
    blend = " (blend)" if champ["blend"] else ""
    lines.append(f"Champion: {len(champ['members'])} member(s){blend},"
                 f" log loss on all scored seasons {champ['tuning']['log_loss']:.4f}")
    for m in champ["members"]:
        lines.append(f"  {m}")
    return "\n".join(lines)
