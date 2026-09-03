import re
import traceback
from datetime import datetime
from flask import has_request_context, session, request
from db import get_connection

# Patterns to sanitize sensitive data from logs
SENSITIVE_PATTERNS = [
    (re.compile(r'(password|passwd|pwd|master_password|secret|token|hash)[\'":\s=]+([^\s,;\'"&]+)', re.IGNORECASE), r'\1: [REDACTED]'),
    (re.compile(r'scrypt:[^\s,;\'"]+', re.IGNORECASE), '[REDACTED_HASH]'),
    (re.compile(r'pbkdf2:[^\s,;\'"]+', re.IGNORECASE), '[REDACTED_HASH]'),
]

def sanitize_text(text):
    """Removes sensitive credentials, passwords, and tokens from log messages."""
    if not text:
        return text
    clean_text = str(text)
    for pattern, replacement in SENSITIVE_PATTERNS:
        clean_text = pattern.sub(replacement, clean_text)
    return clean_text

class AuditService:
    """
    Central server-side audit and error logging service for BHIPL Operation Portal.
    Stores records directly in SQL Server tables:
      - UserActionLog
      - ApplicationErrorLog
    """

    @staticmethod
    def _extract_context():
        """Extracts contextual information from Flask request/session if available."""
        ctx = {
            "user_id": None,
            "username": None,
            "role": None,
            "ip": None,
            "user_agent": None,
            "path": None,
            "method": None
        }

        if has_request_context():
            try:
                ctx["user_id"] = session.get("user_id")
                # Prefer full_name (e.g. 'Ragavendra') or emp_id
                full_name = session.get("full_name")
                emp_id = session.get("emp_id")
                if full_name and emp_id:
                    ctx["username"] = f"{full_name} ({emp_id})"
                elif full_name:
                    ctx["username"] = full_name
                elif emp_id:
                    ctx["username"] = emp_id

                ctx["role"] = session.get("role")
                ctx["ip"] = request.headers.get("X-Forwarded-For", request.remote_addr)
                if ctx["ip"] and "," in ctx["ip"]:
                    ctx["ip"] = ctx["ip"].split(",")[0].strip()
                ctx["user_agent"] = request.headers.get("User-Agent", "")[:490]
                ctx["path"] = request.path[:490]
                ctx["method"] = request.method[:15]
            except Exception:
                pass

        return ctx

    @classmethod
    def log_action(cls, action, module, description=None, ref_type=None, ref_id=None,
                   user_id=None, username=None, role=None, ip=None, user_agent=None):
        """
        Records a user action in UserActionLog.
        Safe against failures: will not raise exceptions that break calling workflows.
        """
        try:
            ctx = cls._extract_context()
            final_user_id = user_id if user_id is not None else ctx["user_id"]
            final_username = username if username is not None else ctx["username"]
            final_role = role if role is not None else ctx["role"]
            final_ip = ip if ip is not None else ctx["ip"]
            final_user_agent = (user_agent if user_agent is not None else ctx["user_agent"]) or ""
            if len(final_user_agent) > 490:
                final_user_agent = final_user_agent[:490]

            final_desc = sanitize_text(description)
            final_action = str(action).strip().upper()[:100]
            final_module = str(module).strip()[:100]
            final_ref_type = str(ref_type).strip()[:100] if ref_type else None
            final_ref_id = str(ref_id).strip()[:100] if ref_id else None

            # Always use a dedicated connection for audit logging to preserve transaction isolation
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO UserActionLog
                (UserId, Username, Role, Action, Module, Description, ReferenceType, ReferenceId, IPAddress, UserAgent, CreatedAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE())
            """, (
                final_user_id,
                final_username,
                final_role,
                final_action,
                final_module,
                final_desc,
                final_ref_type,
                final_ref_id,
                final_ip,
                final_user_agent
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[AuditService.log_action ERROR]: {e}")
            return False

    @classmethod
    def log_error(cls, message, error=None, module=None, action=None, ref_id=None,
                  level="ERROR", user_id=None, username=None, role=None,
                  path=None, method=None, ip=None):
        """
        Records an exception or error in ApplicationErrorLog.
        Captures sanitized stack traces and metadata.
        """
        try:
            ctx = cls._extract_context()
            final_user_id = user_id if user_id is not None else ctx["user_id"]
            final_username = username if username is not None else ctx["username"]
            final_role = role if role is not None else ctx["role"]
            final_ip = ip if ip is not None else ctx["ip"]
            final_path = path if path is not None else ctx["path"]
            final_method = method if method is not None else ctx["method"]

            exc_type = None
            stack_trace = None
            if error:
                exc_type = type(error).__name__
                stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            elif error is None:
                # If in an except block, traceback.format_exc() gets current exception
                curr_tb = traceback.format_exc()
                if curr_tb and "NoneType: None" not in curr_tb:
                    stack_trace = curr_tb

            clean_message = sanitize_text(message)
            clean_stack = sanitize_text(stack_trace) if stack_trace else None
            final_module = str(module).strip()[:100] if module else "General"
            final_action = str(action).strip().upper()[:100] if action else "SYSTEM"
            final_ref_id = str(ref_id).strip()[:100] if ref_id else None

            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO ApplicationErrorLog
                (Timestamp, Level, Message, ExceptionType, StackTrace, UserId, Username, Role, Module, Action, RequestPath, HTTPMethod, IP, ReferenceId, CreatedAt)
                VALUES (GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE())
            """, (
                str(level).upper()[:20],
                clean_message,
                exc_type[:200] if exc_type else None,
                clean_stack,
                final_user_id,
                final_username,
                final_role,
                final_module,
                final_action,
                final_path,
                final_method,
                final_ip,
                final_ref_id
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[AuditService.log_error ERROR]: {e}")
            return False
