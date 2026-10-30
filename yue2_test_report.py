"""Build a reproducible Fix9 report from explicit audits and pytest JUnit artifacts."""
from __future__ import annotations

import argparse
import collections
import json
import logging
import math
import re
import statistics
import textwrap
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from yue2_test_support import BASELINE, ROOT, dependency, verify_manifest

LOGGER = logging.getLogger(__name__)


def main() -> None:
    """Summarize per-file outcomes without refreshing baseline or source fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "doc" / "yue2_auto_instrumental_fix9_report.md")
    args = parser.parse_args()
    verify_manifest()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    silence_path = ROOT / "doc" / "yue2_auto_instrumental_fix9_silence.json"
    silence = json.loads(silence_path.read_text(encoding="utf-8"))
    applicable = sum("silent_bars" in row for row in silence.values())
    report = ["# Fix9: символические тесты YuE2", "", "Корпус: 406 файлов; manifest проверен по SHA-256.", "",
              f"SBL: `{dependency('YuE2Instrumental.py').relative_to(ROOT).as_posix()}`.",
              f"Checker: `{dependency('upstream_abc_tools.py').relative_to(ROOT).as_posix()}`.", "",
              "Конвертер и эталонные Python-файлы не изменялись. Baseline снят явной командой до regression-прогона.",
              "Независимый checker запускается и в corpus, и в extended; его отсутствие является ошибкой.", "",
              "## Запуски", "", "```powershell",
              "python -m pytest -c pytest_yue2.ini -m unit -q",
              "python -m pytest -c pytest_yue2.ini -m corpus -q",
              "python -m pytest -c pytest_yue2.ini -m extended --representative -q",
              "python -m pytest -c pytest_yue2.ini -m extended -q", "```", "",
              "Обновление manifest: `python yue2_test_support.py update-manifest`.",
              "Baseline обновляется только `capture-baseline --error-run PATH --smart-run PATH` после review.", "",
              f"Строго корректных источников для oracle тишины: {applicable}; остальные не имеют достоверной",
              "полной исходной временной шкалы. Метрики записаны в `yue2_auto_instrumental_fix9_silence.json`.", "",
              "## Результаты pytest", ""]
    report += ["Времена относятся к testcase вместе с аудитом и записью артефактов.",
               "Актуальная повторная проверка corpus с числовым gate: `fix9_corpus_verified.xml`.",
               "Первый `fix9_corpus.xml` снят до добавления этого gate; оба результата оставлены для анализа.", ""]
    failure_counts: collections.Counter[str] = collections.Counter()
    matrix_failures: collections.Counter[str] = collections.Counter()
    duration_cases: dict[str, tuple[float, float]] = {}
    checker_failures = 0
    for path in sorted((ROOT / "tmp").glob("fix9_*.xml")):
        if path.name == "fix9_corpus_final.xml":
            continue  # Superseded infrastructure diagnostic with colliding artifact directories.
        tree = ET.parse(path)
        tests = tree.findall(".//testcase")
        failed = [t for t in tests if t.find("failure") is not None or t.find("error") is not None]
        skipped = sum(t.find("skipped") is not None for t in tests)
        properties = [p.attrib for t in tests for p in t.findall("./properties/property")]
        not_applicable = sum(p.get("name") == "applicable" and p.get("value") == "false" for p in properties)
        times = sorted(float(t.attrib.get("time", 0)) for t in tests)
        report += [f"- `{path.name}`: passed={len(tests) - len(failed) - skipped}, "
                   f"failed={len(failed)}, skipped={skipped}, not-applicable={not_applicable}."]
        if times:
            p95 = times[max(0, math.ceil(len(times) * .95) - 1)]
            report += [f"  Median={statistics.median(times):.6f}s, p95={p95:.6f}s, max={max(times):.6f}s."]
        for case in failed:
            if path.name == "fix9_extended_full.xml":
                source_match = re.search(r"YuE2_ABC_\d+", case.attrib.get("name", ""))
                if source_match:
                    matrix_failures[source_match.group() + ".txt"] += 1
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            message = failure.attrib.get("message", "") if failure is not None else "unknown"
            if path.name == "fix9_extended_full.xml":
                if "AbcError" in message:
                    checker_failures += 1
                else:
                    values = re.search(r"assert ([0-9.]+) == ([0-9.]+)", message)
                    if source_match and values:
                        duration_cases[source_match.group() + ".txt"] = (
                            float(values.group(1)), float(values.group(2))
                        )
            failure_counts[message] += 1
    report += ["", "## Сравнение с Fix8", ""]
    for mode in ("error", "smart"):
        records = list(baseline[mode].values())
        full = [r for r in records if r["conversion_status"] in ("OK", "SMART_RECOVERED")]
        accepted = sum(r["upstream_status"] == "accepted" for r in full)
        extensions = sum(r["vocal_duration_extensions"] for r in records)
        report += [f"- {mode}: full={len(full)}, upstream accepted={accepted}, Vocal extensions={extensions}."]
    report += ["", "Ориентир Fix8: error=262; smart=300; checker smart=280; 17 продлений в 12 файлах.",
               "Агрегаты не заменяют per-file regression gates.", "", "## Все исходники", "",
               "| File | error | smart | checker smart | extensions | uncertainty | matrix failures |",
               "|---|---|---|---|---:|---|---:|"]
    for name, smart in baseline["smart"].items():
        normal = baseline["error"][name]
        report += [f"| {name} | {normal['conversion_status']} | {smart['conversion_status']} | "
                   f"{smart['upstream_status']} | {smart['vocal_duration_extensions']} | "
                   f"{smart['musical_uncertainty']} | {matrix_failures[name]} |"]
    report += ["", "## Неоднозначные полные восстановления", ""]
    ambiguous = [name for name, row in baseline["smart"].items()
                 if row["musical_uncertainty"] == "True"
                 and row["conversion_status"] in ("OK", "SMART_RECOVERED")]
    report += [textwrap.fill(", ".join(ambiguous), width=110), "",
               "Подробные статусы, длительности, bars и проверки по каждому источнику: `tests/yue2_baseline.json`.",
               "Каждая конфигурация и её отказ: `tmp/fix9_extended_full.xml`."]
    report += ["", "## Продления Vocal", ""]
    for name, row in baseline["smart"].items():
        changes = row.get("musical_preservation", {}).get("vocal_duration_extensions", [])
        for change in changes:
            report += [textwrap.fill(f"- {name}: {json.dumps(change, ensure_ascii=False)}", width=110,
                                     subsequent_indent="  ")]
    report += ["", "## Отказы extended / regression gates", ""]
    report += [f"Независимый checker: {checker_failures} отказов конфигураций.",
               f"Несогласованная числовая длительность: {len(duration_cases)} источников.", "",
               "| File | reported seconds | serialized seconds | delta |",
               "|---|---:|---:|---:|"]
    for name, (reported, serialized) in sorted(duration_cases.items()):
        report += [f"| {name} | {reported:.9f} | {serialized:.9f} | {serialized - reported:+.9f} |"]
    report += [""]
    for message, count in failure_counts.items():
        report += [textwrap.fill(f"- {count} cases: {message}", width=110, subsequent_indent="  ")]
    report += ["", "## Минимальный дефект восстановления", "",
               "Удаление barline на 25 источниках: 9 точных восстановлений, 2 безопасных raw-отказа,",
               "14 полных восстановлений с отличиями от известного ground truth.",
               "Удаление barline в `^C8|C8|` даёт `^C8C8|`. После восстановления вторая нота",
               "имеет MIDI 61 вместо MIDI 60: альтерация утекла через восстановленную границу такта.",
               "Причина: высота разрешается при разборе объединённого исходного такта до восстановления сетки.",
               "Тест `test_missing_barline_preserves_original_accidental_scope` оставлен failing.",
               "Второй минимальный пример: Vocal `C8|D8|`, Ins `M:5/8 Z3|`, исходный M:4/4, L:1/8,",
               "Q:1/4=120. Возвращено 4 секунды, повторно разобранный выход длится 5 секунд.",
               "`score_seconds` вычисляется до сериализации расширенной сетки, через исходный `_score_end`.",
               "Тест `test_numeric_duration_uses_serialized_grid_end` оставлен failing.",
               "Точные missing/added интервалы всех 25 barline-проб сохранены в",
               "`doc/yue2_auto_instrumental_fix9_mutation_differences.json`.", "",
               "## Добавленные файлы", "",
               "- `yue2_test_support.py`, `yue2_test_report.py`, `pytest_yue2.ini`.",
               "- `tests/conftest.py`, `tests/test_yue2_fix9.py`, `tests/test_yue2_mutations.py`.",
               "- `tests/raw_abc_manifest.json`, `tests/yue2_baseline.json`.",
               "- `.gitattributes`, `.github/workflows/yue2-tests.yml`.",
               "- Три отчёта Fix9: Markdown, JSON тишины, JSON точных mutation-различий.",
               "- Обновлены четыре существующих тестовых/аудиторских модуля и документация.", "",
               "## Ограничения", "",
               "Никакой YuE2-аудиогенерации или GPU-проверки не выполнялось.",
               "Статусы эвристического восстановления и checker acceptance не доказывают музыкальный замысел.",
               "Для повреждённых источников ground truth неизвестен; oracle точно сравнивает строгие источники.",
               "Mutation-набор проверяет порчу нормализованных результатов и 13 видов текстовых изменений.",
               "Отсутствующий в конкретном источнике объект мутации отмечен not-applicable, без SKIP.",
               "Точное восстановление проверено отдельно для удаления barline; остальные неоднозначные",
               "повреждения проверяют способность аудитора обнаружить изменение относительно ground truth.",
               "Результаты Linux CI подтвердятся только после запуска workflow в GitHub Actions.", ""]
    args.output.write_text("\n".join(report), encoding="utf-8")
    LOGGER.info(f"Report written to {args.output}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        main()
    except Exception:
        LOGGER.error(f"Report failed in {ROOT}: {traceback.format_exc()}")
        raise
