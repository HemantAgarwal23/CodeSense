# CodeSense AI benchmark

- Run: 2026-09-14 15:22 UTC
- Cases: 40 (30 with one known bug, 10 clean)
- LLM: openai/gpt-oss-120b (falls back to openai/gpt-oss-20b on errors or rate limits)

| Mode | Recall (bugs found) | Precision (findings that were correct) | Clean snippets flagged | Failed cases |
|---|---|---|---|---|
| static | 27% (8/30) | 80% (8/10) | 1/10 | 0 |
| llm | 97% (29/30) | 88% (35/40) | 3/10 | 0 |
| hybrid | 100% (30/30) | 86% (42/49) | 4/10 | 0 |

## Per case

| Case | static | llm | hybrid |
|---|---|---|---|
| division-by-zero-literal | found | found | found |
| division-by-empty-length | missed | found | found |
| missing-key-on-empty-dict | missed | found | found |
| index-past-end | missed | found | found |
| loop-reads-past-end | missed | found | found |
| regex-match-may-be-none | missed | found | found |
| sql-injection | missed | found | found |
| subprocess-shell-injection | found | found | found |
| eval-on-input | found | found | found |
| hardcoded-api-key | found | missed | found |
| pickle-untrusted-data | missed | found | found |
| file-never-closed | missed | found | found |
| mutable-default-argument | found | found | found |
| exception-swallowed | found | found | found |
| recursion-without-base-case | missed | found | found |
| late-binding-lambda | missed | found | found |
| computed-value-not-returned | found | found | found |
| identity-compare-with-literal | missed | found | found |
| range-check-always-true | missed | found | found |
| unreachable-code | found | found | found |
| list-mutated-while-iterating | missed | found | found |
| variable-maybe-unassigned | missed | found | found |
| string-plus-int | missed | found | found |
| float-equality | missed | found | found |
| insecure-random-token | missed | found | found |
| path-traversal | missed | found | found |
| shared-class-attribute | missed | found | found |
| counter-overwritten | missed | found | found |
| default-evaluated-once | missed | found | found |
| sort-returns-none | missed | found | found |
| clean-typed-add | clean | clean | clean |
| clean-guarded-mean | clean | clean | clean |
| clean-context-manager | 1 false alarm(s) | 1 false alarm(s) | 1 false alarm(s) |
| clean-parameterized-sql | clean | clean | clean |
| clean-factorial | clean | 2 false alarm(s) | 1 false alarm(s) |
| clean-none-default | clean | 1 false alarm(s) | 1 false alarm(s) |
| clean-secure-token | clean | clean | clean |
| clean-dict-get | clean | clean | clean |
| clean-guarded-regex | clean | clean | 1 false alarm(s) |
| clean-filter-comprehension | clean | clean | clean |
