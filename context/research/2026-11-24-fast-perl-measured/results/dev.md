| item | slow case | fast case | slow fuel | fast fuel | ratio | page | within 20% |
|---|---|---|---:|---:|---:|---:|---|
| count matches in a whole file | `fast-count-lines` | `fast-count-whole-file` | 587,243,136 | 64,757,494 | 9.07× | 9× | yes |
| substitute in a whole file | `fast-substitute-lines` | `fast-substitute-whole-file` | 733,300,579 | 97,166,932 | 7.55× | 7.5× | yes |
| keyed sort | `fast-sort-split-in-block` | `fast-sort-schwartzian` | 29,514,543,011 | 5,354,833,117 | 5.51× | 5× | yes |
| JSON through JSON::MaybeXS | `fast-json-pp` | `fast-json-maybexs` | 13,341,791,904 | 507,311,171 | 26.30× | 29× | yes |
| JSON through Cpanel::JSON::XS | `fast-json-pp` | `fast-json-cpanel` | 13,341,791,904 | 465,328,535 | 28.67× | 29× | yes |
| CSV | `fast-csv-parsewords` | `fast-csv-text-csv` | 15,313,473,477 | 1,161,441,020 | 13.18× | 13× | yes |
| count, F's form | `-ne '$n++ if /ERROR/ …'` | `fast-count-whole-file` | 456,681,845 | 64,757,494 | 7.05× | | |

`use POSIX` against `use POSIX ()`: 71,083,395 - 54,799,158 = 16.3 M (page: 16 M)
`scalar @POSIX::EXPORT`: 587 (page: 587)
