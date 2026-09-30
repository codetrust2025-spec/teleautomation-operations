"""Keep secrets that travel in query strings out of the access log.

Uvicorn's access logger writes the raw request line - including the query
string - so any secret passed as a query parameter is written verbatim into the
container log and from there into the Docker json log on disk.

`/api/gmail/pubsub/push` carries `GMAIL_PUBSUB_VERIFICATION_TOKEN` exactly that
way, because Google Pub/Sub push subscriptions cannot send custom headers; the
token has to ride in the URL.  The nginx vhost already sets `access_log off` for
that location for this reason, but that only covers nginx.  Every successful
push still reached uvicorn, which logged the live token one layer down.

Verified in production before this fix: a single successful push wrote the real
token into `/var/lib/docker/containers/<id>/<id>-json.log`.  It stayed hidden
because until then no request had ever succeeded with the correct token - the
failing probes logged only wrong ones.

This does not make the query string safe; it makes the *log* safe.  The token
is still secret-in-a-URL and should stay off any other surface too.
"""

from __future__ import annotations

import logging
import re

# Query parameters whose values must never be written to a log.  Matched
# case-insensitively, up to the next parameter separator or quote.
_SECRET_QUERY_PARAMS = ("token", "access_token", "api_key", "key", "signature", "password", "secret")

_PATTERN = re.compile(
    r"((?:%s)=)[^&\s\"']+" % "|".join(re.escape(p) for p in _SECRET_QUERY_PARAMS),
    re.IGNORECASE,
)

REDACTED = r"\1<redacted>"

# Secrets that ride in the URL *path* rather than the query. A Gmail reconnect
# link is /api/candidate-mailboxes/reconnect/<signed token>; the token names the
# mailbox and carries the account holder's address, so it must not reach the log.
_SECRET_PATH = re.compile(r"(/api/candidate-mailboxes/reconnect/)[^\s\"'?#/]+")
_PATH_MARKER = "/reconnect/"

# Loggers that emit request lines.  uvicorn.access is the one that matters;
# the others are cheap insurance if the server is ever run differently.
_TARGET_LOGGERS = ("uvicorn.access", "gunicorn.access", "hypercorn.access")


def redact(text: str) -> str:
    """Replace the value of any secret-bearing query parameter or path segment."""
    return _SECRET_PATH.sub(REDACTED, _PATTERN.sub(REDACTED, text))


def _worth_scanning(value: str) -> bool:
    return "=" in value or _PATH_MARKER in value


class QuerySecretRedactingFilter(logging.Filter):
    """Strip secret query-parameter values from a log record before it is emitted.

    Access records carry the request line in `record.args`, not in `record.msg`
    (the message is a `%s` template), so both have to be rewritten.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and _worth_scanning(record.msg):
            record.msg = redact(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                redact(a) if isinstance(a, str) and _worth_scanning(a) else a for a in args
            )
        elif isinstance(args, dict):
            record.args = {
                k: redact(v) if isinstance(v, str) and _worth_scanning(v) else v
                for k, v in args.items()
            }
        return True


def install_access_log_redaction() -> None:
    """Attach the filter to every access logger.  Safe to call more than once.

    Called at import time from main.py rather than on the startup event, so the
    filter is in place before the server can serve - and log - its first
    request.
    """
    for name in _TARGET_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, QuerySecretRedactingFilter) for f in logger.filters):
            logger.addFilter(QuerySecretRedactingFilter())
