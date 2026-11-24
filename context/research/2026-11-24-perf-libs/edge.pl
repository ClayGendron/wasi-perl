# Hostile inputs for the XS parsers: each must end in a Perl error the program can catch, never
# a trap. Run under native perl and under wasi-perl; the lines should be equal.
use strict;
use warnings;
use Cpanel::JSON::XS ();
use Text::CSV_XS ();
my $j = Cpanel::JSON::XS->new;
for my $case (["deep 100000", "[" x 100000], ["deep 512", ("[" x 512) . ("]" x 512)],
              ["truncated", '{"a": [1, 2'], ["bad utf8", "[\"\xff\xfe\"]"], ["huge number", "[" . ("9" x 5000) . "]"],
              ["nul", "[\"a\x00b\"]"], ["trailing", "[1] x"]) {
    my ($name, $text) = @$case;
    my $r = eval { $j->decode($text); "ok" } // "died: " . ($@ =~ s/ at .*//sr);
    print "json $name: $r\n";
}
my $c = Text::CSV_XS->new({binary => 1});
for my $case (["unterminated quote", qq{a,"b,c\n}], ["quote in field", qq{a,b"c,d\n}], ["nul", "a,\x00,b\n"],
              ["long field", ("x" x 1_000_000) . ",y\n"]) {
    my ($name, $text) = @$case;
    my $ok = $c->parse($text =~ s/\n\z//r);
    print "csv $name: ", ($ok ? "ok " . scalar($c->fields) : "error " . (0 + $c->error_diag)), "\n";
}
print "json true is JSON::PP::Boolean: ", (Cpanel::JSON::XS::true()->isa("JSON::PP::Boolean") ? 1 : 0), "\n";
print "encode 5.0: ", Cpanel::JSON::XS->new->encode([5 * 1.0, 10 * 0.5, "5"]), "\n";
