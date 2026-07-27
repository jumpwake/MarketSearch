from __future__ import annotations

from marketsearch.fingerprint import fingerprint


def test_identical_inputs_produce_identical_digest():
    a = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    b = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    assert a == b


def test_digest_is_32_hex_chars():
    digest = fingerprint("x", None, None, None)
    assert len(digest) == 32
    assert all(c in "0123456789abcdef" for c in digest)


def test_case_and_whitespace_are_normalised():
    a = fingerprint("2019 BOBCAT   T770", 3_800_000, "Dale S", "Olathe, KS")
    b = fingerprint("  2019 bobcat t770 ", 3_800_000, "dale s", "olathe, ks")
    assert a == b


def test_punctuation_is_ignored():
    a = fingerprint("2019 Bobcat T-770!!", 3_800_000, None, None)
    b = fingerprint("2019 Bobcat T 770", 3_800_000, None, None)
    assert a == b


def test_price_change_produces_different_digest():
    """A repost at a lower price is news and must not be suppressed."""
    a = fingerprint("2019 Bobcat T770", 4_100_000, "Dale S", "Olathe, KS")
    b = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    assert a != b


def test_different_seller_produces_different_digest():
    a = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    b = fingerprint("2019 Bobcat T770", 3_800_000, "Rita M", "Olathe, KS")
    assert a != b


def test_missing_fields_are_stable_not_random():
    a = fingerprint("2019 Bobcat T770", None, None, None)
    b = fingerprint("2019 Bobcat T770", None, None, None)
    assert a == b
