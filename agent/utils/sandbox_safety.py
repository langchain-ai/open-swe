import sqlite3
import os
import time
import logging
import shlex
import re
from pathlib import Path
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from agent.utils.model import make_model
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "agent_safety.db"

def init_db():
    """Initialize SQLite database with audit and approval tables."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Audit trail table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_trail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                command TEXT,
                risk_level TEXT,
                reason TEXT,
                duration REAL,
                exit_code INTEGER
            )
        """)
        
        # Approvals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                command TEXT,
                risk_level TEXT,
                reason TEXT,
                status TEXT DEFAULT 'PENDING'
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("Sandbox safety SQLite database initialized successfully at %s", DB_PATH)
    except Exception as e:
        logger.exception("Failed to initialize sandbox safety database")

# Initialize DB on import
init_db()

# Static regex patterns for L1 blocklist/risk classification
STATIC_HIGH_RISK_PATTERNS = [
    r"\brm\s+-[rf]*\s*/",                    # rm -rf /
    r"\brm\s+-[rf]*\s+--no-preserve-root",   # rm -rf --no-preserve-root
    r"\bmkfs\b",                             # filesystem formatting
    r"\bdd\s+.*of=/dev/",                    # direct disk raw sector overwrite
    r"\bchmod\s+-[R]*\s+777\s*/",            # open permissions on root
    r"\bchown\s+-[R]*\s+.*777",              # chown open permissions
    r"\bshutdown\b",                         # shutting down host machine
    r"\breboot\b",                           # rebooting host machine
    r"\bpoweroff\b",                         # powering off host machine
]

STATIC_MEDIUM_RISK_PATTERNS = [
    r"\brm\s+-[rf]+",                        # generic rm -rf
    r"\bcurl\b",                             # network requests (potential exfiltration/remote script runs)
    r"\bwget\b",                             # generic wget
    r"\bgit\s+push\b.*--force",              # destructive git push
    r"\bkill\s+-[0-9a-zA-Z]+",               # process termination
    r"\bkillall\b",                          # process termination
    r"\bpkill\b",                            # process termination
    r"\bsh\b\s+<",                           # redirecting file into shell
    r"\bbash\b\s+<",
]

def classify_command_statically(command: str) -> tuple[str, str]:
    """Classify command statically using regex rules.
    
    Returns (risk_level, reason).
    """
    for pattern in STATIC_HIGH_RISK_PATTERNS:
        if re.search(pattern, command):
            return "HIGH", "Matched critical blocked command pattern."
            
    for pattern in STATIC_MEDIUM_RISK_PATTERNS:
        if re.search(pattern, command):
            return "MEDIUM", "Matched medium-risk file deletion or network execution pattern."
            
    return "LOW", "No dangerous static patterns matched."

def classify_command_with_llm(command: str) -> tuple[str, str]:
    """Evaluate complex/ambiguous commands using a hybrid Static + LLM approach.
    
    Returns (risk_level, reason) where risk_level is 'LOW', 'MEDIUM', or 'HIGH'.
    """
    # Step 1: Run fast static checks first
    static_level, static_reason = classify_command_statically(command)
    if static_level == "HIGH":
        return "HIGH", f"[Static Rule] {static_reason}"
        
    # Skip LLM analysis for simple commands to save token cost and lower latency
    try:
        words = shlex.split(command) if command else []
    except Exception:
        words = command.split() if command else []
        
    if len(words) <= 3 and static_level == "LOW":
        return "LOW", "Simple developer utility command."

    # Step 2: Query a lightweight model for contextual validation
    model_id = os.environ.get("LLM_MODEL_ID", "openai:gpt-5.5")
    try:
        model = make_model(model_id, temperature=0.0)
        
        prompt = f"""You are an enterprise AI security firewall. You audit shell commands executed by an AI agent in a Linux sandbox.
Analyze this command:
```bash
{command}
```

Classify its risk level:
- HIGH: Commands that block/destroy infrastructure, exfiltrate private credentials, or execute obfuscated dangerous payloads (e.g., base64 decodes, malicious bash injection).
- MEDIUM: Commands that install raw software packages, push code with potential override flags, fetch raw network scripts, delete source files, or perform network requests to arbitrary servers.
- LOW: Standard safe developer actions (e.g., listing directories, viewing files, building, compiling, checking git status, running standard pytest/npm test suites).

Respond in EXACTLY the following JSON format:
{{
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "reason": "Brief explanation of safety review"
}}"""

        response = model.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        
        # Extract json content and parse
        import json
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            level = data.get("risk_level", "LOW").upper()
            reason = data.get("reason", "Analyzed by LLM firewall.")
            if level in ("LOW", "MEDIUM", "HIGH"):
                return level, f"[LLM] {reason}"
        
    except Exception as e:
        logger.warning("LLM Command classification failed or credentials missing: %s. Falling back to static evaluation.", e)
        return static_level, f"[Static Fallback] {static_reason}"
        
    return static_level, f"[Static Fallback] {static_reason}"


class AuditingSandboxWrapper:
    """A proxy wrapper that intercepts shell commands executed in the sandbox.
    
    Provides logging of all executed commands into an SQLite audit trail
    and halts medium-risk commands pending manual human approval.
    """
    def __init__(self, raw_sandbox: SandboxBackendProtocol):
        self._raw_sandbox = raw_sandbox

    @property
    def id(self) -> str:
        return self._raw_sandbox.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        start_time = time.time()
        
        # 1. Audit classification
        risk_level, reason = classify_command_with_llm(command)
        
        # 2. Block/Approval Handling
        if risk_level == "HIGH":
            duration = time.time() - start_time
            self._log_audit(command, risk_level, reason, duration, exit_code=1)
            return ExecuteResponse(
                output=f"[SECURITY BLOCKED] Command rejected by Enterprise Shield Firewall.\nReason: {reason}",
                exit_code=1,
                truncated=False
            )
            
        elif risk_level == "MEDIUM":
            # Insert approval record
            approval_id = self._insert_approval(command, risk_level, reason)
            
            # Console notification
            print("\n" + "="*80)
            print(f"⚠️  [SECURITY WARNING] PENDING HUMAN APPROVAL (ID: {approval_id})")
            print(f"Command: {command}")
            print(f"Reason:  {reason}")
            print(f"Action:  Run 'python scripts/approve_cmd.py --approve {approval_id}' in another terminal to authorize.")
            print("="*80 + "\n")
            
            # Polling loop waiting for manual approval
            approved = False
            timeout_limit = 300  # 5 minutes
            poll_interval = 1.0
            elapsed = 0.0
            
            while elapsed < timeout_limit:
                status = self._check_approval_status(approval_id)
                if status == "APPROVED":
                    approved = True
                    break
                elif status == "REJECTED":
                    break
                time.sleep(poll_interval)
                elapsed += poll_interval
                
            if not approved:
                # Set as TIMEOUT if still pending
                self._update_approval_status(approval_id, "TIMEOUT")
                duration = time.time() - start_time
                self._log_audit(command, risk_level, f"[REJECTED/TIMEOUT] {reason}", duration, exit_code=1)
                return ExecuteResponse(
                    output=f"[SECURITY REJECTED] Command execution rejected or timed out by human approval (ID: {approval_id}).",
                    exit_code=1,
                    truncated=False
                )
        
        # 3. Execution of safe or approved command
        try:
            res = self._raw_sandbox.execute(command, timeout=timeout)
            duration = time.time() - start_time
            self._log_audit(command, risk_level, reason, duration, res.exit_code)
            return res
        except Exception as e:
            duration = time.time() - start_time
            self._log_audit(command, risk_level, f"Failed with exception: {e}", duration, exit_code=1)
            raise

    def __getattr__(self, name):
        """Delegate all other standard properties/file methods to raw sandbox."""
        return getattr(self._raw_sandbox, name)

    def _log_audit(self, command: str, risk_level: str, reason: str, duration: float, exit_code: int):
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_trail (command, risk_level, reason, duration, exit_code)
                VALUES (?, ?, ?, ?, ?)
            """, (command, risk_level, reason, duration, exit_code))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Failed to insert audit trail log: %s", e)

    def _insert_approval(self, command: str, risk_level: str, reason: str) -> int:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO approvals (command, risk_level, reason, status)
                VALUES (?, ?, ?, 'PENDING')
            """, (command, risk_level, reason))
            conn.commit()
            approval_id = cursor.lastrowid
            conn.close()
            return approval_id
        except Exception as e:
            logger.error("Failed to register pending safety approval: %s", e)
            return 0

    def _check_approval_status(self, approval_id: int) -> str:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return row[0]
        except Exception as e:
            logger.error("Failed to query safety approval status: %s", e)
        return "PENDING"

    def _update_approval_status(self, approval_id: int, status: str):
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE approvals SET status = ? WHERE id = ?", (status, approval_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Failed to update safety approval status: %s", e)
