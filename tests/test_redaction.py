from cua.policy.redaction import redact


def test_redact_ssn_and_password():
    text = "ssn 123-45-6789 password: hunter2"
    out = redact(text)
    assert "123-45-6789" not in out
    assert "hunter2" not in out
    assert "[SSN]" in out
