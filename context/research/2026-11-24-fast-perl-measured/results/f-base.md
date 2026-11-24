| item | slow case | fast case | slow fuel | fast fuel | ratio | page | within 20% |
|---|---|---|---:|---:|---:|---:|---|
| count matches in a whole file | `fast-count-lines` | `fast-count-whole-file` | 697,133,555 | 116,755,468 | 5.97× | 9× | NO |
| substitute in a whole file | `fast-substitute-lines` | `fast-substitute-whole-file` | 842,881,226 | 149,488,952 | 5.64× | 7.5× | NO |
| keyed sort | `fast-sort-split-in-block` | `fast-sort-schwartzian` | 29,546,078,688 | 5,389,102,437 | 5.48× | 5× | yes |
| count, F's form | `-ne '$n++ if /ERROR/ …'` | `fast-count-whole-file` | 565,021,326 | 116,755,468 | 4.84× | | |

`use POSIX` against `use POSIX ()`: 72,178,370 - 55,840,124 = 16.3 M (page: 16 M)
`scalar @POSIX::EXPORT`: 587 (page: 587)
