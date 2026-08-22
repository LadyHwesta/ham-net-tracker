"""
Tests for send_email()'s low-level SMTP mechanics (as opposed to the
higher-level flows in test_email_verification.py / test_reminders.py, which
monkeypatch send_email() itself and never touch smtplib).
"""

import main


class TestSMTPTimeout:
    """send_email() previously called smtplib.SMTP()/SMTP_SSL() with no
    timeout at all -- a dead/unreachable SMTP host would hang the request
    that triggered it (e.g. registration) indefinitely instead of failing
    fast (issue #1 follow-up)."""

    def test_smtp_call_passes_a_timeout(self, monkeypatch, smtp_configured):
        captured = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                captured["host"], captured["port"], captured["timeout"] = host, port, timeout

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, user, password):
                pass

            def sendmail(self, from_addr, to, msg):
                pass

        monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
        ok = main.send_email(to=["x@example.com"], subject="Test", body_html="<p>hi</p>")
        assert ok is True
        assert captured["timeout"] == main.SMTP_TIMEOUT_SECONDS
        assert captured["timeout"] is not None

    def test_smtp_ssl_call_passes_a_timeout(self, monkeypatch, smtp_configured):
        captured = {}

        class FakeSMTPSSL:
            def __init__(self, host, port, timeout=None):
                captured["timeout"] = timeout

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, user, password):
                pass

            def sendmail(self, from_addr, to, msg):
                pass

        monkeypatch.setattr(main, "SMTP_USE_SSL", True)
        monkeypatch.setattr(main.smtplib, "SMTP_SSL", FakeSMTPSSL)
        ok = main.send_email(to=["x@example.com"], subject="Test", body_html="<p>hi</p>")
        assert ok is True
        assert captured["timeout"] == main.SMTP_TIMEOUT_SECONDS
        assert captured["timeout"] is not None
