"""A live secret must never reach the access log.

`/api/gmail/pubsub/push` authenticates with `GMAIL_PUBSUB_VERIFICATION_TOKEN`
passed as a query parameter, because Google Pub/Sub push subscriptions cannot
send custom headers - the token has to ride in the URL.  The nginx vhost sets
`access_log off` for that location, but nginx is only the first layer: uvicorn's
own access logger writes the raw request line, query string included.

Verified in production before this fix.  The very first push that arrived with
a *correct* token wrote it verbatim into the container's Docker json log:

    INFO: 172.18.0.1:39240 - "POST /api/gmail/pubsub/push?token=<real> HTTP/1.1" 200 OK

It had gone unnoticed because every earlier probe used a deliberately wrong
token, so the logs only ever held values that were worthless.  A working
integration is what turns this from noise into a disclosed credential.

The first test reproduces that exact record shape.
"""

from __future__ import annotations

import logging

from core.log_redaction import (
    QuerySecretRedactingFilter,
    install_access_log_redaction,
    redact,
)

SECRET = "s3cr3t-live-token-value-do-not-log"


def _access_record(path: str) -> logging.LogRecord:
    """A record shaped exactly like uvicorn.access emits.

    The request line lives in `record.args`, not in `record.msg` - a filter that
    only rewrote the message would look correct and still leak.
    """
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("172.18.0.1:39240", "POST", path, "1.1", 200),
        exc_info=None,
    )


def test_the_production_leak_is_redacted():
    """The regression, reproduced from the real logged line."""
    record = _access_record("/api/gmail/pubsub/push?token=" + SECRET)

    assert QuerySecretRedactingFilter().filter(record) is True

    rendered = record.getMessage()
    assert SECRET not in rendered
    assert "token=<redacted>" in rendered
    # The rest of the line must survive - it is what makes the log useful.
    assert "/api/gmail/pubsub/push" in rendered
    assert "POST" in rendered and "200" in rendered


def test_a_filter_that_only_cleaned_the_message_would_not_be_enough():
    """Guards the subtlety above: the secret arrives via args."""
    record = _access_record("/api/gmail/pubsub/push?token=" + SECRET)
    QuerySecretRedactingFilter().filter(record)
    assert all(SECRET not in a for a in record.args if isinstance(a, str))


def test_other_secret_parameter_names_are_covered():
    for name in ("token", "access_token", "api_key", "key", "signature", "password", "secret"):
        assert SECRET not in redact("/x?%s=%s" % (name, SECRET))


def test_redaction_is_case_insensitive():
    assert SECRET not in redact("/x?TOKEN=" + SECRET)
    assert SECRET not in redact("/x?Access_Token=" + SECRET)


def test_it_stops_at_the_parameter_boundary():
    """Redacting greedily to end-of-line would destroy the rest of the line."""
    out = redact("/api/gmail/pubsub/push?token=%s&messageId=abc123" % SECRET)
    assert SECRET not in out
    assert "messageId=abc123" in out, "following parameters must survive"


def test_a_request_line_without_secrets_is_untouched():
    line = '/api/candidate-mailboxes/health?verbose=1'
    assert redact(line) == line


def test_installation_is_idempotent():
    """main.py imports once, but tests and reloads must not stack filters."""
    logger = logging.getLogger("uvicorn.access")
    logger.filters = [f for f in logger.filters if not isinstance(f, QuerySecretRedactingFilter)]

    install_access_log_redaction()
    install_access_log_redaction()
    install_access_log_redaction()

    installed = [f for f in logger.filters if isinstance(f, QuerySecretRedactingFilter)]
    assert len(installed) == 1


def test_the_filter_is_actually_installed_on_the_access_logger():
    install_access_log_redaction()
    logger = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, QuerySecretRedactingFilter) for f in logger.filters)


def test_an_emitted_access_record_reaches_a_handler_already_redacted():
    """End to end through the logging machinery, not just the filter in isolation."""
    install_access_log_redaction()
    logger = logging.getLogger("uvicorn.access")
    logger.setLevel(logging.INFO)
    captured: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = Capture()
    logger.addHandler(handler)
    try:
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "172.18.0.1:39240",
            "POST",
            "/api/gmail/pubsub/push?token=" + SECRET,
            "1.1",
            200,
        )
    finally:
        logger.removeHandler(handler)

    assert captured, "nothing was logged"
    assert SECRET not in captured[0]
    assert "token=<redacted>" in captured[0]


def test_main_installs_redaction_before_the_app_is_created():
    """Ordering matters: a filter installed after the first request is too late."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    install_line = None
    app_line = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "install_access_log_redaction"
        ):
            install_line = node.lineno
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "app" and app_line is None:
                    app_line = node.lineno

    assert install_line is not None, "main.py never installs access-log redaction"
    assert app_line is not None, "main.py no longer assigns `app`"
    assert install_line < app_line, "redaction must be installed before the app exists"


# --- a secret in the URL path: the Gmail reconnect link ---------------------------

RECONNECT = "/api/candidate-mailboxes/reconnect/"
TOKEN = "eyJwIjoiZ21haWwtcmVjb25uZWN0LXYxIn0.0123456789abcdef"


def test_a_reconnect_token_in_the_path_is_redacted():
    line = f'127.0.0.1:1 - "GET {RECONNECT}{TOKEN} HTTP/1.1" 302 Found'
    assert TOKEN not in redact(line)
    assert f"{RECONNECT}<redacted> HTTP/1.1" in redact(line)


def test_the_access_record_shape_is_redacted_through_the_filter():
    """Uvicorn passes the request line as an argument, not in the message."""
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", f"{RECONNECT}{TOKEN}", "1.1", 302), None,
    )
    assert QuerySecretRedactingFilter().filter(record) is True
    assert TOKEN not in record.getMessage()
    assert "<redacted>" in record.getMessage()


def test_other_paths_are_left_alone():
    line = 'GET /api/candidate-mailboxes/overview HTTP/1.1'
    assert redact(line) == line
    assert redact("GET /api/candidate-mailboxes/reconnect-status HTTP/1.1").endswith("reconnect-status HTTP/1.1")
