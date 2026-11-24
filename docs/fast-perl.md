# Fast Perl

Some common ways to write a Perl program cost several times more than an equal one in the sandbox. This page says which form to use.

Each number is a ratio of guest instructions (fuel), so it holds on any machine. It was measured on inputs of 1.7 to 10 MB. Every example on the page is a check: it runs in `tests/oneliners/` and must print what native perl 5.42.3 prints, and every number is checked to within 20%.

## Whole-file regex work: `-0777`

When the pattern does not depend on lines, read the file whole with `-0777`. A count of matches runs 9× fewer instructions than a line loop, and a substitution 7.5× fewer, because the loop costs more than the regex.

```sh
perl -0777 -ne '$n++ while /ERROR/g; print "$n\n"' app.log
perl -0777 -pi -e 's/ERROR/WARN/g' app.log
```

instead of

```sh
perl -ne '$n++ while /ERROR/g; END { print "$n\n" }' app.log
perl -pi -e 's/ERROR/WARN/g' app.log
```

In a whole file, `^` and `$` match only at its start and end. Add `/m` to match them at every line.

## Keyed sorts: a Schwartzian transform

Split each line once, before the sort, not twice for each comparison inside the `sort` block. It runs 5× fewer instructions.

```sh
perl -e 'print map { $_->[1] } sort { $b->[0] <=> $a->[0] } map { [(split /,/)[2], $_] } <>' data.csv
```

instead of

```sh
perl -e 'print sort { (split /,/, $b)[2] <=> (split /,/, $a)[2] } <>' data.csv
```

## JSON: `JSON::MaybeXS` or `Cpanel::JSON::XS`

Both decode with Cpanel::JSON::XS, which is compiled in. JSON::PP is pure Perl. It decodes in 29× the instructions.

```sh
perl -MJSON::MaybeXS -0777 -ne 'print "$_->{name}\n" for grep { $_->{active} } @{ decode_json($_) }' users.json
perl -MCpanel::JSON::XS -0777 -ne 'print "$_->{name}\n" for grep { $_->{active} } @{ decode_json($_) }' users.json
perl -MCpanel::JSON::XS -0777 -ne 'my $j = Cpanel::JSON::XS->new->canonical; print $j->encode([grep { $_->{active} } @{ $j->decode($_) }]), "\n"' users.json
```

instead of

```sh
perl -MJSON::PP -0777 -ne 'print "$_->{name}\n" for grep { $_->{active} } @{ decode_json($_) }' users.json
```

## CSV: `Text::CSV`

`Text::CSV` parses with Text::CSV_XS, which is compiled in. It reads quoted fields, doubled quotes and newlines inside a field. It runs 13× fewer instructions than `Text::ParseWords`, which drops doubled quotes and reads one line at a time. `split /,/` breaks a quoted field that holds a comma.

```sh
perl -MText::CSV -e 'my $csv = Text::CSV->new({binary => 1}); open my $fh, "<", "people.csv" or die "people.csv: $!\n"; while (my $row = $csv->getline($fh)) { print "$row->[1]\n" }'
perl -MText::CSV -e 'my $csv = Text::CSV->new({binary => 1}); open my $fh, "<", "people.csv" or die "people.csv: $!\n"; while (my $row = $csv->getline($fh)) { print join("|", map { s/\n/\\n/gr } @$row), "\n" }'
```

instead of

```sh
perl -MText::ParseWords -ne 'chomp; my @f = parse_line(",", 0, $_); print "$f[1]\n"' people.csv
```

## Imports: `use POSIX ()`

Write `use POSIX ()`, or `use POSIX qw(floor ceil)` for the names you call. A bare `use POSIX` imports 587 names, which costs 16 M instructions on every run. On the command line, `-mPOSIX` is `use POSIX ()`.

```sh
perl -mPOSIX -le 'print POSIX::floor(-2.5)'
perl -MPOSIX=floor,ceil -le 'print floor(-2.5), " ", ceil(-2.5)'
```

instead of

```sh
perl -MPOSIX -le 'print POSIX::floor(-2.5)'
```

## `File::Find`, and anything that calls `chdir`: give `run()` a `cwd`

Without a `cwd`, the guest starts at `/`, which no grant serves. `find` over an absolute folder then dies at its end with "Can't cd to /: No such file or directory", exit 2. Over `.` it finds nothing. Name a folder inside a grant:

```python
from wasi_perl import Grant, run

done = run(
    [
        "-MFile::Find",
        "-e",
        r'my @f; find(sub { push @f, $File::Find::name if -f }, "."); print "$_\n" for sort @f',
    ],
    grants=[Grant("/path/to/project", "/work")],
    cwd="/work",
)
```

`wasi-perl run --cwd /work` does the same.
