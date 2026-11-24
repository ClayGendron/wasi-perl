"""Checks on the XS modules linked into perl.wasm and on the library folder beside it.

Each module must load, and each must do one real thing with the same stdout, stderr and exit
status as the native perl 5.42.3 built from the same tarball. The library must hold no file a
sandboxed run never reads, and MANIFEST and SHA256SUMS must describe the artifacts exactly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from conftest import ARTIFACTS, DEV, DEV_GUEST, LIBRARY, LIBRARY_GUEST, ROOT

# The extensions linked statically, as %Config names them.
STATIC_EXT = sorted(
    [
        "B",
        "Compress/Raw/Bzip2",
        "Compress/Raw/Zlib",
        "Cwd",
        "Data/Dumper",
        "Devel/Peek",
        "Digest/MD5",
        "Digest/SHA",
        "Encode",
        "Encode/Byte",
        "Encode/CN",
        "Encode/EBCDIC",
        "Encode/JP",
        "Encode/KR",
        "Encode/Symbol",
        "Encode/TW",
        "Encode/Unicode",
        "Fcntl",
        "File/DosGlob",
        "File/Glob",
        "Filter/Util/Call",
        "Hash/Util",
        "Hash/Util/FieldHash",
        "I18N/Langinfo",
        "IO",
        "List/Util",
        "MIME/Base64",
        "Math/BigInt/FastCalc",
        "Opcode",
        "POSIX",
        "PerlIO/encoding",
        "PerlIO/via",
        "SDBM_File",
        "Socket",
        "Storable",
        "Sys/Hostname",
        "Time/HiRes",
        "Time/Piece",
        "Unicode/Collate",
        "Unicode/Normalize",
        "attributes",
        "mro",
        "re",
    ]
)

# The extensions left out, each because it cannot work under WASI or is never installed.
LEFT_OUT = sorted(
    [
        "threads",
        "threads/shared",
        "IPC/SysV",
        "Sys/Syslog",
        "DB_File",
        "GDBM_File",
        "NDBM_File",
        "ODBM_File",
        "PerlIO/mmap",
        "Devel/PPPort",
        "XS/APItest",
        "XS/Typemap",
        "Thread/Queue",
        "Thread/Semaphore",
    ]
)

# Every module that must load: the XS modules, the modules they come with, and the pure-Perl
# ones the specification names.
MODULES = [
    "POSIX",
    "List::Util",
    "Scalar::Util",
    "Sub::Util",
    "Data::Dumper",
    "Storable",
    "Encode",
    "Encode::Byte",
    "Encode::CN",
    "Encode::JP",
    "Encode::KR",
    "Encode::TW",
    "Encode::Symbol",
    "Encode::Unicode",
    "Encode::EBCDIC",
    "Digest::MD5",
    "Digest::SHA",
    "MIME::Base64",
    "Time::HiRes",
    "Cwd",
    "File::Spec",
    "JSON::PP",
    "Fcntl",
    "IO",
    "IO::File",
    "IO::Handle",
    "IO::Select",
    "File::Glob",
    "File::DosGlob",
    "Hash::Util",
    "Hash::Util::FieldHash",
    "mro",
    "re",
    "attributes",
    "PerlIO::scalar",
    "PerlIO::encoding",
    "PerlIO::via",
    "PerlIO::via::QuotedPrint",
    "Opcode",
    "Safe",
    "B",
    "B::Deparse",
    "Devel::Peek",
    "Filter::Util::Call",
    "SDBM_File",
    "Sys::Hostname",
    "I18N::Langinfo",
    "Unicode::Normalize",
    "Unicode::Collate",
    "Math::BigInt::FastCalc",
    "Compress::Raw::Zlib",
    "Compress::Raw::Bzip2",
    "Compress::Zlib",
    "IO::Compress::Gzip",
    "IO::Uncompress::Gunzip",
    "Time::Piece",
    "Socket",
    "File::Temp",
    "File::Find",
    "File::Path",
    "File::Copy",
    "File::Basename",
    "Getopt::Long",
    "Text::Wrap",
    "Time::Local",
    "Tie::File",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_loads(guest, native, module):
    prog = "print qq{ok\\n}"
    g, n = guest(f"-M{module}", "-e", prog), native(f"-M{module}", "-e", prog)
    assert (n.code, n.stdout) == (0, b"ok\n")
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


def test_config_agrees_with_the_list(guest):
    prog = (
        "use Config; print join(q{ }, sort split q{ }, $Config{static_ext}), qq{\\n};"
        "print qq{[$Config{dynamic_ext}]\\n};"
        "my %e = map { $_ => 1 } split q{ }, $Config{extensions};"
        "print join(q{ }, sort grep { $e{$_} } @ARGV), qq{\\n};"
    )
    r = guest("-e", prog, *STATIC_EXT, *LEFT_OUT)
    assert r.code == 0 and r.stderr == b""
    static, dynamic, in_extensions = r.stdout.decode().splitlines()
    assert static.split() == STATIC_EXT
    assert dynamic.strip("[] ") == ""
    assert in_extensions.split() == STATIC_EXT


def test_config_describes_wasm32(guest):
    prog = (
        "use Config; my %s = map { (split /=/)[0] => 1 } split / /, $Config{cppsymbols};"
        'print join(",", map { $s{$_} ? 1 : 0 } qw(__wasm32__ __wasi__ _ILP32 __x86_64__ __linux__)), "\\n";'
        'print join("\\n", grep { index($_, $ARGV[0]) >= 0 } map { "$_=$Config{$_}" } sort keys %Config), "\\n";'
    )
    # %Config holds no build-host path: the library's bytes must not depend on the checkout.
    r = guest("-e", prog, str(ROOT))
    assert (r.code, r.stderr) == (0, b"")
    assert r.stdout == b"1,1,1,0,0\n\n"


# The distributions whose name is no module, and the module that stands for each.
DISTRIBUTION_MODULE = {
    "libnet": "Net::Cmd",
    "podlators": "Pod::Man",
    "IO/Compress": "IO::Compress::Gzip",
    "autouse": "autouse qw(Carp croak)",
    "if": "if 1, 'strict'",
}

# Modules of the library that load natively and not in the sandbox, each with its reason.
FAILS_IN_THE_SANDBOX = {
    # Test2 and CPAN copy STDOUT and STDERR with open(">&"), which needs dup(); WASI preview1
    # has no call that duplicates a descriptor.
    "Test/Simple": b"Can't dup",
    "CPAN": b"dup failed",
    "Term/Table": b"Could not clone STDOUT",
}


def test_every_listed_extension_loads_as_natively(guest, native):
    r = guest("-MConfig", "-e", 'print "$_\\n" for split / /, $Config{extensions}')
    extensions = r.stdout.decode().split()
    assert "POSIX" in extensions and "JSON/PP" in extensions
    for ext in extensions:
        module = DISTRIBUTION_MODULE.get(ext, ext.replace("/", "::"))
        prog = f"use {module}; print qq{{ok\\n}}"
        g, n = guest("-e", prog), native("-e", prog)
        assert (n.code, n.stdout) == (0, b"ok\n"), (ext, n.stderr)
        if ext in FAILS_IN_THE_SANDBOX:
            assert g.code != 0 and FAILS_IN_THE_SANDBOX[ext] in g.stderr, (
                ext,
                g.stderr,
            )
        else:
            assert (g.code, g.stdout) == (0, b"ok\n"), (ext, g.stderr)


@pytest.mark.parametrize(
    "module",
    [
        "threads",
        "threads::shared",
        "Thread::Queue",
        "IPC::SysV",
        "Sys::Syslog",
        "DB_File",
        "PerlIO::mmap",
    ],
)
def test_left_out_module_does_not_load(guest, module):
    r = guest(f"-M{module}", "-e", "1")
    assert r.code != 0 and r.stdout == b""
    assert b"Can't locate" in r.stderr


# Programs whose output must equal native perl's, byte for byte.
SAME_AS_NATIVE = {
    "list-util": (
        "use List::Util qw(sum max first uniq reduce shuffle pairs);"
        'print sum(1 .. 10), " ", max(3, 9, 2), " ", first { $_ > 3 } 1 .. 9;'
        'print " ", join(",", uniq(1, 1, 2, 3, 3)), " ", reduce { $a * $b } 1 .. 6;'
        'print " ", scalar(() = shuffle 1 .. 5), " ", join(";", map { "@$_" } pairs(a => 1, b => 2)), "\\n";'
    ),
    "scalar-util": (
        "use Scalar::Util qw(blessed reftype looks_like_number weaken dualvar);"
        'my $o = bless {}, "Foo"; print blessed($o), " ", reftype($o), " ", reftype([]), " ";'
        'print join(",", map { looks_like_number($_) ? 1 : 0 } 1, "1e5", "abc", " 12 ", "0x10"), " ";'
        'my $r = { }; my $w = $r; weaken($w); undef $r; print defined $w ? "live" : "gone";'
        'my $d = dualvar(5, "five"); print " ", $d + 0, " $d\\n";'
    ),
    "posix": (
        "use POSIX qw(floor ceil fmod strftime INT_MAX DBL_MAX EXIT_FAILURE);"
        'print floor(-2.5), " ", ceil(2.1), " ", fmod(10, 3), " ", INT_MAX, " ", EXIT_FAILURE, "\\n";'
        'print strftime("%Y-%m-%d %H:%M:%S %a %b %j", gmtime(1700000000)), "\\n";'
        'print POSIX::isatty(0) ? "tty" : "notty", " ", POSIX::WEXITSTATUS(3 << 8), "\\n";'
        'my @t = POSIX::strtol("0x1f", 16); print "@t\\n";'
    ),
    "posix-strtol-junk": (
        'use POSIX (); my @r = POSIX::strtol("junk", 10); print "@r\\n";'
        'my $n = POSIX::strtol("42abc", 10); print "$n\\n";'
        'eval { POSIX::strtol() }; print $@ =~ /^Usage: POSIX::strtol/ ? "usage\\n" : "no: $@\\n";'
    ),
    "data-dumper": (
        "use Data::Dumper; $Data::Dumper::Sortkeys = 1; $Data::Dumper::Indent = 1;"
        'print Dumper({ b => [1, 2, { c => undef }], a => "x\\ty", n => 1.5, s => \\"ref" });'
        '$Data::Dumper::Useqq = 1; $Data::Dumper::Terse = 1; print Dumper("caf\\x{e9}\\n");'
    ),
    "storable": (
        "use Storable qw(freeze thaw dclone nfreeze); use Data::Dumper;"
        "$Data::Dumper::Sortkeys = 1; $Storable::canonical = 1;"
        'my $d = { list => [1 .. 5, [6, { seven => 7 }]], name => "x", n => 2.5, u => undef };'
        "print Dumper(thaw(freeze($d))), Dumper(dclone($d));"
        'print unpack("H*", nfreeze($d)), "\\n";'
        'my $o = bless { k => [1] }, "Obj"; my $c = dclone($o); print ref $c, " $c->{k}[0]\\n";'
    ),
    "storable-corrupt": (
        "use Storable qw(nfreeze thaw);"
        "my $f = nfreeze([1 .. 100]); substr($f, 12) = q{};"
        'my $r = eval { thaw($f) }; print defined $r ? "thawed\\n" : "undef\\n"; print "err: $@";'
        'eval { thaw("\\x05\\x0bjunk") }; print "junk: ", ($@ ? "died\\n" : "no error\\n");'
        'print "after\\n";'
    ),
    "encode": (
        "use Encode qw(encode decode find_encoding);"
        'my $s = "caf\\x{e9} \\x{263a} \\x{65e5}\\x{672c}";'
        'print unpack("H*", encode("UTF-8", $s)), "\\n";'
        'print unpack("H*", encode("iso-8859-1", "caf\\x{e9}")), "\\n";'
        'print unpack("H*", encode("shiftjis", "\\x{65e5}\\x{672c}\\x{8a9e}")), "\\n";'
        'print decode("UTF-8", "\\xe2\\x98\\xba") eq "\\x{263a}" ? "ok" : "no", " ";'
        'print decode("shiftjis", "\\x93\\xfa\\x96\\x7b") eq "\\x{65e5}\\x{672c}" ? "ok" : "no", " ";'
        'print ord(decode("latin1", "\\xe9")), "\\n";'
        "for my $e (qw(cp1252 euc-jp euc-kr big5 gb2312 UTF-16LE cp1047 MacRoman)) {"
        '  print "$e ", find_encoding($e)->name, " ", unpack("H*", encode($e, "A\\x{e9}")), "\\n" }'
    ),
    "encode-layer": (
        'open my $fh, ">:encoding(UTF-16BE)", \\my $buf or die; print $fh "hi\\x{263a}"; close $fh;'
        'print unpack("H*", $buf), "\\n";'
        'open $fh, "<:encoding(UTF-16BE)", \\$buf or die; my $l = <$fh>; print length($l), "\\n";'
    ),
    "digests": (
        "use Digest::MD5 qw(md5_hex); use Digest::SHA qw(sha1_hex sha256_hex sha512_hex);"
        'print md5_hex("abc"), "\\n", sha1_hex("abc"), "\\n", sha256_hex("abc"), "\\n";'
        'print sha512_hex(""), "\\n"; my $c = Digest::MD5->new; $c->add("a" x 1000);'
        'print $c->hexdigest, " ", Digest::SHA->new(384)->add("x")->hexdigest, "\\n";'
    ),
    "base64": (
        "use MIME::Base64; use MIME::QuotedPrint;"
        'my $e = encode_base64(join("", map { chr } 0 .. 255)); print $e;'
        'print decode_base64($e) eq join("", map { chr } 0 .. 255) ? "round trip\\n" : "broken\\n";'
        'print encode_qp("caf\\xe9 = ok"), "\\n";'
    ),
    "file-spec": (
        "use File::Spec; use File::Basename;"
        'print File::Spec->catfile("a", "b", "c.txt"), " ", File::Spec->rel2abs("x/y", "/base"), " ";'
        'print File::Spec->abs2rel("/a/b/c", "/a"), " ", File::Spec->canonpath("/a/./b//c/"), " ";'
        'print join(",", File::Spec->splitdir("/a/b")), " ", basename("/x/y.pl"), " ", dirname("/x/y.pl"), "\\n";'
    ),
    "json-pp": (
        "use JSON::PP; my $j = JSON::PP->new->canonical;"
        'my $t = $j->encode({ b => [1, "two", undef, \\1, \\0], a => { "c d" => 1.25 }, u => "\\x{263a}" });'
        'print $t, "\\n"; my $d = $j->decode(q({"k":[1,2,{"x":null}],"s":"\\u00e9"}));'
        'print scalar @{ $d->{k} }, " ", defined $d->{k}[2]{x} ? "def" : "null", " ", ord $d->{s}, "\\n";'
        'eval { $j->decode("{bad") }; print $@ ? "bad json caught\\n" : "no error\\n";'
    ),
    "die-through-first": (
        "use List::Util qw(first any);"
        'my $r = eval { first { die "in first\\n" } 1 .. 3 }; print "first: $@";'
        'eval { first { my @x = sort { die "in sort\\n" } 3, 1, 2; 1 } 1 }; print "sort: $@";'
        'eval { any { die { code => 7 } } 1 }; print "object: $@->{code}\\n";'
        'print "after: ", (first { $_ > 1 } 1 .. 3), "\\n";'
    ),
    "hash-util": (
        "use Hash::Util qw(lock_keys unlock_keys lock_hash);"
        'my %h = (a => 1); lock_keys(%h); eval { $h{b} = 2 }; print $@ =~ /disallowed/ ? "locked\\n" : "open: $@\\n";'
        'unlock_keys(%h); $h{b} = 2; print join(",", sort keys %h), "\\n";'
    ),
    "mro-re-attributes": (
        "use mro; { package A; sub hi { 'A' } package B; our @ISA = ('A'); package C; our @ISA = ('A');"
        "package D; use mro 'c3'; our @ISA = ('B', 'C'); }"
        'print join(",", @{ mro::get_linear_isa("D") }), "\\n";'
        'use re qw(is_regexp regname); my $q = qr/(?<w>\\w+)/; print is_regexp($q) ? "re" : "no", " ";'
        '"abc" =~ $q; print regname("w"), "\\n";'
        'use attributes (); sub lv :lvalue { my $x } print join(",", attributes::get(\\&lv)), "\\n";'
    ),
    "perlio-layers": (
        'open my $fh, "<", \\"line1\\nline2\\n" or die; my @l = <$fh>; print scalar @l, "\\n";'
        'open my $out, ">", \\my $buf or die; print $out "x" x 3; close $out; print "$buf\\n";'
        'use PerlIO::via::QuotedPrint; open my $q, ">:via(QuotedPrint)", \\my $qp or die; print $q "caf\\xe9\\n"; close $q; print $qp;'
        'print join(",", PerlIO::get_layers(*STDOUT)), "\\n";'
    ),
    "opcode-safe-b": (
        "use Safe; my $s = Safe->new; print $s->reval('2 * 21'), \"\\n\";"
        'my $r = $s->reval(q{open my $f, "<", "/etc/passwd"}); print $@ =~ /trapped/ ? "trapped\\n" : "not: $@\\n";'
        'use B qw(svref_2object); sub named {} print svref_2object(\\&named)->GV->NAME, "\\n";'
        'use B::Deparse; print B::Deparse->new->coderef2text(sub { $_[0] + 1 }) =~ /\\$_\\[0\\] \\+ 1/ ? "deparse\\n" : "no\\n";'
    ),
    "unicode": (
        "use Encode (); use Unicode::Normalize qw(NFD NFC NFKC); use Unicode::Collate;"
        'print unpack("H*", Encode::encode("UTF-8", NFD("\\x{e9}"))), " ", length(NFC("e\\x{301}")), " ", NFKC("\\x{fb01}"), "\\n";'
        'print join(" ", Unicode::Collate->new->sort(qw(b A a B \\x{e9} e))), "\\n";'
    ),
    "bigint-fastcalc": (
        "use Math::BigInt lib => 'FastCalc'; print Math::BigInt->config('lib'), \"\\n\";"
        'print Math::BigInt->new(2)->bpow(100), " ", Math::BigInt->new(100)->bfac->length, "\\n";'
    ),
    "compress": (
        "use Compress::Zlib; use Compress::Raw::Bzip2; use IO::Compress::Gzip qw(gzip);"
        "use IO::Uncompress::Gunzip qw(gunzip);"
        'my $t = "hello " x 100; my $c = compress($t); print length($c), " ", uncompress($c) eq $t ? "zlib ok" : "zlib bad";'
        'print " ", crc32("abc"), " ", adler32("abc"), "\\n";'
        'gzip(\\$t => \\my $gz, Minimal => 1) or die; gunzip(\\$gz => \\my $back) or die; print $back eq $t ? "gzip ok\\n" : "gzip bad\\n";'
        'my ($bz, $st) = Compress::Raw::Bzip2->new(0, 9, 0); my $out = ""; $bz->bzdeflate($t, $out); $bz->bzclose($out);'
        'my ($bu) = Compress::Raw::Bunzip2->new(0, 1); my $plain = ""; $bu->bzinflate($out, $plain);'
        'print $plain eq $t ? "bzip2 ok" : "bzip2 bad", " ", unpack("H*", substr($out, 0, 4)), "\\n";'
    ),
    "time-piece": (
        "use Time::Piece; use Time::Seconds; my $t = gmtime(1700000000);"
        'print $t->ymd, " ", $t->hms, " ", $t->year, " ", $t->day_of_week, " ", $t->strftime("%Y%m%d"), "\\n";'
        'my $u = $t + ONE_DAY; print $u->ymd, " ", ($u - $t), "\\n";'
        'print Time::Piece->strptime("2024-02-29 12:00", "%Y-%m-%d %H:%M")->epoch, "\\n";'
    ),
    "fcntl-io": (
        "use Fcntl qw(:seek :mode); use IO::Handle; use IO::File;"
        'print SEEK_SET, SEEK_CUR, SEEK_END, " ", S_ISDIR(040755) ? "dir" : "no", " ", S_ISREG(0100644) ? "reg" : "no", "\\n";'
        'my $h = IO::Handle->new; print ref $h, " ", STDOUT->can("autoflush") ? "can" : "cannot", "\\n";'
    ),
    "filter-fieldhash-peek": (
        "use Hash::Util::FieldHash qw(fieldhash); fieldhash my %fh; my $k = {}; $fh{$k} = 1;"
        'print scalar keys %fh, " "; undef $k; print scalar keys %fh, "\\n";'
        'use Devel::Peek qw(SvREFCNT); my $x = 1; print SvREFCNT($x), "\\n";'
    ),
    "socket-constants": (
        "use Socket qw(AF_INET SOCK_STREAM inet_aton inet_ntoa pack_sockaddr_in unpack_sockaddr_in);"
        'print inet_ntoa(inet_aton("127.0.0.1")), " ", length(inet_aton("10.1.2.3")), "\\n";'
    ),
    "time-hires": (
        "use Time::HiRes qw(time sleep usleep gettimeofday tv_interval);"
        "my @t = map { time } 1 .. 50; my $up = 1; $up &&= $t[$_] >= $t[$_ - 1] for 1 .. $#t;"
        'print $up ? "non-decreasing" : "went back", "\\n"; my $a = time; sleep(0.01); my $b = time;'
        'print $b > $a ? "advanced\\n" : "stuck\\n"; my $t0 = [gettimeofday]; usleep(1000);'
        'print tv_interval($t0) > 0 ? "interval\\n" : "zero\\n";'
        'print time =~ /\\./ ? "fraction\\n" : "whole\\n";'
    ),
}


@pytest.mark.parametrize("name", sorted(SAME_AS_NATIVE))
def test_same_as_native(guest, native, name):
    prog = SAME_AS_NATIVE[name]
    g, n = guest("-e", prog), native("-e", prog)
    assert n.code == 0, n.stderr
    assert (g.code, g.stdout, g.stderr) == (n.code, n.stdout, n.stderr)


@pytest.mark.parametrize("code", [0, 3, 42])
def test_posix_exit(guest, native, code):
    prog = f'$| = 1; print "before\\n"; POSIX::_exit({code}); print "never\\n"'
    g, n = guest("-MPOSIX", "-e", prog), native("-MPOSIX", "-e", prog)
    assert (n.code, n.stdout) == (code, b"before\n")
    assert (g.code, g.stdout, g.stderr, g.status) == (code, b"before\n", b"", code)


def _work(tmp_path: Path) -> list[tuple[Path, str, bool]]:
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    for name in ("b.txt", "a.txt", "c.log", "sub/d.txt"):
        (work / name).write_text(name)
    return [
        (LIBRARY, LIBRARY_GUEST, False),
        (DEV, DEV_GUEST, False),
        (work, "/work", False),
    ]


def test_cwd_under_a_granted_pwd(guest, tmp_path):
    prog = (
        "use Cwd qw(getcwd cwd abs_path); use File::Spec;"
        'print getcwd(), " ", cwd(), " ", abs_path("sub"), " ", File::Spec->rel2abs("x"), "\\n";'
        'chdir "sub" or die "chdir: $!"; print getcwd(), "\\n";'
    )
    r = guest("-e", prog, env={"PWD": "/work"}, grants=_work(tmp_path))
    assert (r.code, r.stderr) == (0, b"")
    assert r.stdout == b"/work /work /work/sub /work/x\n/work/sub\n"


def test_glob(guest, tmp_path):
    prog = 'print join(" ", glob("*.txt")), "|", join(" ", <*/*.txt>), "|", join(" ", glob("{c,a}.*")), "\\n"'
    r = guest("-e", prog, env={"PWD": "/work"}, grants=_work(tmp_path))
    assert (r.code, r.stdout, r.stderr) == (
        0,
        b"a.txt b.txt|sub/d.txt|c.log a.txt\n",
        b"",
    )


def test_sdbm_on_a_writable_grant(guest, native, tmp_path):
    prog = (
        "use Fcntl; use SDBM_File; tie my %h, 'SDBM_File', 'db', O_RDWR | O_CREAT, 0644 or die \"tie: $!\";"
        '$h{$_} = $_ * 2 for 1 .. 100; untie %h; tie my %r, "SDBM_File", "db", O_RDONLY, 0 or die;'
        'print scalar(keys %r), " ", $r{50}, "\\n";'
    )
    work = tmp_path / "rw"
    work.mkdir()
    grants = [
        (LIBRARY, LIBRARY_GUEST, False),
        (DEV, DEV_GUEST, False),
        (work, "/rw", True),
    ]
    r = guest("-e", prog, env={"PWD": "/rw"}, grants=grants)
    assert (r.code, r.stdout, r.stderr) == (0, b"100 100\n", b"")


def test_library_holds_no_build_only_files():
    lib = LIBRARY
    if not lib.exists():
        pytest.skip("build/artifacts/lib is missing; run scripts/build.sh")
    unwanted = [
        p
        for p in lib.rglob("*")
        if p.name in ("CORE", "pod", ".packlist")
        or p.suffix in (".pod", ".a", ".bs")
        or "/man/" in str(p)
    ]
    assert unwanted == []
    assert (lib / "5.42" / "JSON" / "PP.pm").is_file()
    assert (lib / "5.42" / "wasm32-wasi" / "List" / "Util.pm").is_file()
    assert (lib / "5.42" / "wasm32-wasi" / "Config.pm").is_file()


def _sums(path: Path) -> dict[str, str]:
    entries = {}
    for line in path.read_text().splitlines():
        digest, sep, name = line.partition("  ")
        assert sep and len(digest) == 64 and name and not name.startswith("/"), line
        entries[name] = digest
    return entries


def test_manifest_lists_every_library_file():
    manifest = ARTIFACTS / "MANIFEST"
    if not manifest.exists():
        pytest.skip("build/artifacts/MANIFEST is missing; run scripts/build.sh")
    entries = _sums(manifest)
    on_disk = sorted(
        str(p.relative_to(ARTIFACTS))
        for top in ("lib", "dev")
        for p in (ARTIFACTS / top).rglob("*")
        if p.is_file()
    )
    assert sorted(entries) == on_disk
    # Sorted by path component, as Python sorts paths, so the runner's writer gives the same bytes.
    assert list(entries) == sorted(entries, key=lambda n: n.split("/"))
    for name, digest in entries.items():
        assert hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest() == digest, (
            name
        )


def test_sha256sums_covers_every_file_of_the_set():
    sums = ARTIFACTS / "SHA256SUMS"
    if not sums.exists():
        pytest.skip("build/artifacts/SHA256SUMS is missing; run scripts/build.sh")
    entries = _sums(sums)
    every_file = sorted(
        str(p.relative_to(ARTIFACTS))
        for p in ARTIFACTS.rglob("*")
        if p.is_file() and p != sums
    )
    assert "perl.wasm" in entries and "MANIFEST" in entries
    assert sorted(entries) == every_file
    assert list(entries) == sorted(entries, key=lambda n: n.split("/"))
    for name, digest in entries.items():
        assert hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest() == digest, (
            name
        )
