# For core modules that have a C (XS) path and a pure-Perl path, print which one is in use.
# Run under native perl and under wasi-perl; the lines should be equal.
use strict;
use warnings;
no strict "refs";
use B ();
use Config;

sub xs { my $c = \&{ $_[0] }; defined &{ $_[0] } ? (B::svref_2object($c)->XSUB ? "XS" : "Perl") : "missing" }

require Data::Dumper;
require File::Spec::Unix;
require Cwd;
require List::Util;
require Scalar::Util;
require Storable;
require Digest::MD5;
require Digest::SHA;
require MIME::Base64;
require Encode;
require Unicode::Normalize;
require Time::HiRes;
require POSIX;
require Math::BigInt;
require Hash::Util;
require Time::Local;
require JSON::PP;

print "Data::Dumper::Dumpxs ", xs("Data::Dumper::Dumpxs"), " Useperl=", (${"Data::Dumper::Useperl"} // 0), "\n";
print "File::Spec::Unix::canonpath ", xs("File::Spec::Unix::canonpath"), "\n";
print "File::Spec::Unix::catfile ", xs("File::Spec::Unix::catfile"), "\n";
print "File::Spec::Unix::catdir ", xs("File::Spec::Unix::catdir"), "\n";
print "Cwd::getcwd ", xs("Cwd::getcwd"), "\n";
print "Cwd::abs_path ", xs("Cwd::abs_path"), "\n";
print "List::Util::sum ", xs("List::Util::sum"), "\n";
print "List::Util::uniq ", xs("List::Util::uniq"), "\n";
print "Scalar::Util::blessed ", xs("Scalar::Util::blessed"), "\n";
print "Storable::pstore ", xs("Storable::pstore"), "\n";
print "Digest::MD5::md5_hex ", xs("Digest::MD5::md5_hex"), "\n";
print "Digest::SHA::sha256_hex ", xs("Digest::SHA::sha256_hex"), "\n";
print "MIME::Base64::encode_base64 ", xs("MIME::Base64::encode_base64"), "\n";
print "Encode::decode_utf8 ", xs("Encode::decode_utf8"), "\n";
print "Unicode::Normalize::NFC ", xs("Unicode::Normalize::NFC"), "\n";
print "Time::HiRes::time ", xs("Time::HiRes::time"), "\n";
print "POSIX::floor ", xs("POSIX::floor"), "\n";
print "Hash::Util::lock_keys ", xs("Hash::Util::lock_keys"), " (Perl wrapper over XS _clear_placeholders: ", xs("Hash::Util::_clear_placeholders"), ")\n";
Math::BigInt->new(1); print "Math::BigInt lib ", Math::BigInt->config()->{lib}, "\n";
print "Time::Local::timegm ", xs("Time::Local::timegm"), "\n";
print "JSON::PP::decode_json ", xs("JSON::PP::decode_json"), "\n";
my %static = map { $_ => 1 } split ' ', $Config{static_ext};
my %dyn = map { $_ => 1 } split ' ', $Config{dynamic_ext};
print "static_ext ", scalar(keys %static), " dynamic_ext ", scalar(keys %dyn), "\n";
print "xs_ext ", join(" ", sort(keys %static, keys %dyn)), "\n";
