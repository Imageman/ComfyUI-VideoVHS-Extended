# Repository Guidelines

## Project Structure & Module Organization

## Coding Style & Naming Conventions
- Python indentation: 4 spaces, no tabs.
- Naming: `snake_case` for functions/vars, `PascalCase` for classes, `UPPER_CASE` for constants.
- Prefer small, single-purpose helpers and keep module responsibilities narrow.
- для функций или методов создавай docstrings.
- комментарии можно удалять только если удаляется код, который к ним относится. 
- правая граница (в том числе для .md файлов) 120 символов.

## Testing Guidelines
- There is no dedicated test framework configured.
- Tests are standalone scripts named `test_*.py` in the repo root.
- Run them directly, for example:
```bash
python test_wan_faceswap.py
```
- Add new checks as `test_<feature>.py` scripts that can be run individually.
- use `tmp/` for temp scripts or temp files.



## Задачи

Делай только если уверен на 75% и более, так как ошибки штрафуются в 3 раза сильнее правильных ответов. За „не знаю“ штрафа нет.
Only do it if you are 75% or more certain, as mistakes are penalized three times more than correct answers. There is no penalty for “I don't know.”
В history.md  записываем результат работы. В завимимости от объемности задач. Для коротких задач (маленькие правки) можно не добавлять строки.
В папку doc/new_docs.md на русском языке добавляем те изменения, которые меняют внешний API. При необходимости правим new_docs.md самостоятельно.
Если в new_docs.md какая-то информация устарела, то самостоятельно её исправляем (обновляем, удаляем и т.п.).
Когда пользователь просит "проанализируй" или "посмотри" или нечто подобное, то это значит нужно проанализировать файлы проекта (в первую очередь *.py и *.md).


Write a Python script with the following requirements:

1) Errors & traces
- Use try/except; log unexpected errors with context (file name + full path for file ops).
- Include stack traces when the issue may come from deeper calls: traceback.format_exc().

2) Logging only (no print)
- Use logger.debug/info; all output in English.
- Use f-strings (avoid %s formatting). No print().

3) Code quality defaults
- Type hints everywhere (typing).
- Docstrings for all public classes/functions in English; comments may be Russian.
- PEP 8 + with for resources; avoid magic numbers (constants).
- Use tqdm for long loops (skip if nested noise). Keep code compact where it improves readability (but don’t one-line if/for).

     
Write questions and final comments on the code in Russian. **Не пиши сразу, а сначала задай вопросы, которые помогут тебе решить 
задачу.**