import time
from collections import defaultdict, deque
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from database.connection import get_pool


# =========================================================
# RATE LIMIT CONFIGURATION
# =========================================================

DEFAULT_MAX_REQUESTS = 10
DEFAULT_WINDOW_SECONDS = 60

ADMIN_MAX_REQUESTS = 30
ADMIN_WINDOW_SECONDS = 60


# =========================================================
# IN-MEMORY RATE LIMITER
# =========================================================

_request_history = defaultdict(
    deque
)


def check_rate_limit(
    user_id: int,
    action: str,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> bool:

    now = time.monotonic()

    key = (
        user_id,
        action,
    )

    history = _request_history[key]

    while history:
        if (
            now - history[0]
            > window_seconds
        ):
            history.popleft()
        else:
            break

    if len(history) >= max_requests:
        return False

    history.append(now)

    return True


# =========================================================
# ADMIN RATE LIMIT
# =========================================================

def check_admin_rate_limit(
    user_id: int,
    action: str,
) -> bool:

    return check_rate_limit(
        user_id=user_id,
        action=f"admin:{action}",
        max_requests=ADMIN_MAX_REQUESTS,
        window_seconds=ADMIN_WINDOW_SECONDS,
    )


# =========================================================
# USER RATE LIMIT
# =========================================================

def check_user_rate_limit(
    user_id: int,
    action: str,
) -> bool:

    return check_rate_limit(
        user_id=user_id,
        action=f"user:{action}",
        max_requests=DEFAULT_MAX_REQUESTS,
        window_seconds=DEFAULT_WINDOW_SECONDS,
    )


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(
    user_id: int,
) -> bool:

    return user_id in ADMIN_IDS


# =========================================================
# PROTECTED ADMIN DECORATOR
# =========================================================

def admin_only(
    action: str = "command",
):
    def decorator(func):

        @wraps(func)
        async def wrapper(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            *args,
            **kwargs,
        ):

            user = update.effective_user

            if not user:
                return

            if not is_admin(user.id):

                if update.message:
                    await update.message.reply_text(
                        "❌ You are not authorized "
                        "to use this command."
                    )

                elif update.callback_query:
                    await update.callback_query.answer(
                        "Not authorized.",
                        show_alert=True,
                    )

                return

            if not check_admin_rate_limit(
                user.id,
                action,
            ):

                if update.message:
                    await update.message.reply_text(
                        "⚠️ Too many requests.\n\n"
                        "Please wait a moment and "
                        "try again."
                    )

                elif update.callback_query:
                    await update.callback_query.answer(
                        "Too many requests. "
                        "Please wait.",
                        show_alert=True,
                    )

                return

            return await func(
                update,
                context,
                *args,
                **kwargs,
            )

        return wrapper

    return decorator


# =========================================================
# SECURITY-SENSITIVE TEXT FILTER
# =========================================================

FORBIDDEN_SECRET_TERMS = (
    "seed phrase",
    "seedphrase",
    "private key",
    "mnemonic",
    "recovery phrase",
    "secret phrase",
)


def contains_secret_request(
    text: str,
) -> bool:

    if not text:
        return False

    lowered = text.lower()

    return any(
        term in lowered
        for term in FORBIDDEN_SECRET_TERMS
    )


# =========================================================
# SANITIZE LOG TEXT
# =========================================================

def sanitize_log_text(
    text: str,
) -> str:

    if not text:
        return ""

    lowered = text.lower()

    for term in FORBIDDEN_SECRET_TERMS:

        if term in lowered:
            return "[REDACTED SECURITY-SENSITIVE TEXT]"

    return text[:1000]


# =========================================================
# AUDIT LOG
# =========================================================

async def audit_log(
    action: str,
    user_id: int = None,
    group_id: int = None,
    details: str = None,
):
    """
    Writes security/audit information to the
    existing system settings table.

    The database schema intentionally does not
    contain a separate audit_logs table yet.
    This function therefore writes concise
    operational information to stdout.

    A dedicated audit table can be added later
    without changing the command behavior.
    """

    safe_details = sanitize_log_text(
        details or ""
    )

    log_message = (
        "[AUDIT] "
        f"action={action} "
        f"user_id={user_id} "
        f"group_id={group_id} "
        f"details={safe_details}"
    )

    print(log_message)


# =========================================================
# SECURITY CHECK
# =========================================================

async def security_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    user = update.effective_user

    if not user:
        return False

    if not check_user_rate_limit(
        user.id,
        "security",
    ):
        return False

    return True


# =========================================================
# CLEAR USER RATE LIMIT
# =========================================================

def clear_rate_limit(
    user_id: int,
    action: str,
):
    key = (
        user_id,
        action,
    )

    _request_history.pop(
        key,
        None,
    )


# =========================================================
# CLEAR ALL RATE LIMIT DATA
# =========================================================

def clear_all_rate_limits():
    _request_history.clear()
