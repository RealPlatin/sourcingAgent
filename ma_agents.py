from __future__ import annotations

import json
import re
import signal
import sys
import time
import datetime
import os
import requests
try:
    import openai as _openai_lib
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False
from dataclasses import dataclass, field
from urllib.parse import urlparse
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- ENV + API CLIENTS ---
load_dotenv()
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
CONFIG_FILE = 'config.json'
LOG_FILE = 'Session_Log.md'
DISCOVERY_COUNT = 10
MAX_CONSECUTIVE_FAILURES = 5
COST_PERPLEXITY = 0.005
PERPLEXITY_RETRIES = 2
SMART_RETRY_MAX = 2


# ============================================================
# 1. CENTRAL CONFIG — TargetCriteria (loaded from .env)
# ============================================================
@dataclass
class TargetCriteria:
    rev_min: float = 0
    rev_max: float = 0
    emp_min: int = 0
    emp_max: int = 0
    rev_per_emp_min: float = 0
    rev_per_emp_max: float = 0
    forbidden_ownership: list = field(default_factory=list)
    required_roles: list = field(default_factory=list)

    @classmethod
    def from_env(cls):
        return cls(
            rev_min=float(os.getenv("REV_MIN", 0)),
            rev_max=float(os.getenv("REV_MAX", 0)),
            emp_min=int(os.getenv("EMP_MIN", 0)),
            emp_max=int(os.getenv("EMP_MAX", 0)),
            rev_per_emp_min=float(os.getenv("REV_PER_EMP_MIN", 0)),
            rev_per_emp_max=float(os.getenv("REV_PER_EMP_MAX", 0)),
            forbidden_ownership=[x.strip().lower() for x in os.getenv("FORBIDDEN_OWNERSHIP", "").split(",") if x.strip()],
            required_roles=[x.strip() for x in os.getenv("REQUIRED_ROLES", "").split(",") if x.strip()],
        )

    def revenue_label(self):
        if self.rev_min and self.rev_max:
            return f"{self.rev_min / 1e6:.0f}-{self.rev_max / 1e6:.0f}M EUR"
        elif self.rev_min:
            return f">{self.rev_min / 1e6:.0f}M EUR"
        elif self.rev_max:
            return f"<{self.rev_max / 1e6:.0f}M EUR"
        return "any revenue"

    def employee_label(self):
        if self.emp_min and self.emp_max:
            return f"{self.emp_min}-{self.emp_max}"
        elif self.emp_min:
            return f">{self.emp_min}"
        elif self.emp_max:
            return f"<{self.emp_max}"
        return "any size"


CRITERIA = TargetCriteria.from_env()

# --- Load config.json for prompt customization ---
_config: dict = {}
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r') as _f:
        _config = json.load(_f)
_active_prompts: dict = {}   # populated at runtime after region selection


def render_template(template: str, **kwargs) -> str:
    """Replace {{variable}} placeholders in a template string.

    Uses double-brace syntax so single braces (e.g. in JSON examples) are
    kept verbatim without escaping.

    Args:
        template: String with {{var_name}} placeholders.
        **kwargs: Key-value pairs to substitute.

    Returns:
        Template with all known placeholders replaced.
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def write_log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {message}\n")


# ============================================================
# 2. GOOGLE SHEETS — Auth + State + Buffer
# ============================================================
def authenticate_google_sheets():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('sheets', 'v4', credentials=creds)


def extract_domain(url_or_name: str) -> str | None:
    """Extract the bare domain from a URL or company website string.

    Args:
        url_or_name: Raw URL or domain string (e.g. "https://www.example.de").

    Returns:
        Lowercase domain without 'www.' prefix, or None if unparseable.
    """
    if not url_or_name or str(url_or_name) in ("NOT FOUND", "UNVERIFIED", ""):
        return None
    s = str(url_or_name).strip().lower()
    if not s.startswith("http"):
        s = "https://" + s
    try:
        parsed = urlparse(s)
        domain = parsed.netloc or parsed.path.split("/")[0]
        domain = domain.replace("www.", "")
        return domain if "." in domain else None
    except Exception:
        return None


class SheetState:
    LEGAL_SUFFIXES = {'gmbh', 'co.', 'kg', 'mbh', 'ohg', 'ag', 'und', 'the', 'and', 'se', 'ug', '&'}
    TABS = ["Targets", "Abgelehnt", "Needs Research"]

    SHEET_HEADERS = [
        "Company Name", "Country", "Sector/Sub-sector", "Website",
        "Short Description", "Why Interesting", "Risks",
        "Estimated Revenue (EUR)", "Employee Count (Est.)",
        "CEO/Founder Name", "CEO Email", "CEO Phone", "Status", "Date Added",
        "Notes", "Quellen / Links"
    ]

    def __init__(self, service, spreadsheet_id):
        self.service = service
        self.sheet_id = spreadsheet_id
        self.headers = self._load_headers()
        self.forbidden_names = set()
        self.forbidden_domains = set()
        self._load_forbidden()
        self.session_buffer = {tab: [] for tab in self.TABS}
        self._committed = False
        self._ensure_tab("Needs Research")
        self._ensure_tab("Abgelehnt")
        self._register_interrupt_handler()

    def _load_headers(self):
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range='Targets!1:1').execute()
        headers = result.get('values', [[]])[0]
        write_log(f"Headers loaded: {headers}")
        return headers

    def _load_forbidden(self):
        website_col_idx = None
        for i, h in enumerate(self.headers):
            if h.lower() == "website":
                website_col_idx = i
                break

        for tab in self.TABS:
            try:
                result = self.service.spreadsheets().values().get(
                    spreadsheetId=self.sheet_id, range=f'{tab}!A:Z').execute()
                rows = result.get('values', [])
                for row in rows[1:]:
                    if not row:
                        continue
                    name = row[0].strip().lower()
                    if name and name not in ("not found", "unknown", ""):
                        self.forbidden_names.add(name)
                    if website_col_idx and website_col_idx < len(row):
                        domain = extract_domain(row[website_col_idx])
                        if domain:
                            self.forbidden_domains.add(domain)
            except Exception:
                pass

        write_log(f"Forbidden loaded: {len(self.forbidden_names)} names, {len(self.forbidden_domains)} domains")

    def _ensure_tab(self, tab_name):
        try:
            meta = self.service.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
            existing_tabs = [s['properties']['title'] for s in meta['sheets']]
            if tab_name not in existing_tabs:
                self.service.spreadsheets().batchUpdate(spreadsheetId=self.sheet_id, body={
                    "requests": [{"addSheet": {"properties": {"title": tab_name}}}]
                }).execute()
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id, range=f'{tab_name}!A1',
                    valueInputOption='USER_ENTERED',
                    body={'values': [self.headers]}
                ).execute()
                write_log(f"Created tab '{tab_name}' with headers")
        except Exception as e:
            write_log(f"WARNING: Could not ensure tab '{tab_name}': {e}")

    def _register_interrupt_handler(self):
        def handler(signum, frame):
            print("\n\n  Interrupt received — committing buffered data...")
            self.commit_session()
            write_log("SESSION INTERRUPTED — buffer committed before exit")
            sys.exit(0)
        signal.signal(signal.SIGINT, handler)

    @staticmethod
    def _name_tokens(name: str) -> set:
        tokens = name.strip().lower().split()
        return {t for t in tokens if len(t) > 2 and t not in SheetState.LEGAL_SUFFIXES}

    def is_duplicate(self, company_name: str, website: str = None) -> tuple[bool, str]:
        """Check if a company already exists in the forbidden set.

        Uses exact match, substring containment, and token-set overlap to
        catch German legal suffix variations and word-order permutations.

        Args:
            company_name: Company name to check.
            website: Optional website URL; domain is also checked against known domains.

        Returns:
            Tuple of (is_dup: bool, reason: str).
        """
        name_lower = company_name.strip().lower()
        if name_lower in ("not found", "unknown", "n/a", ""):
            return False, ""

        for entry in self.forbidden_names:
            if len(entry) < 5:
                continue
            if entry == name_lower:
                return True, f"exact match: '{entry}'"
            if len(entry) > 8 and (entry in name_lower or name_lower in entry):
                return True, f"name match: '{entry}'"

        new_tokens = self._name_tokens(company_name)
        if len(new_tokens) >= 2:
            for entry in self.forbidden_names:
                entry_tokens = self._name_tokens(entry)
                if len(entry_tokens) >= 2 and new_tokens == entry_tokens:
                    return True, f"token match: '{entry}'"
                overlap = new_tokens & entry_tokens
                if len(overlap) >= 2 and len(overlap) >= len(min(new_tokens, entry_tokens, key=len)):
                    return True, f"token overlap: '{entry}'"

        if website:
            domain = extract_domain(website)
            if domain and domain in self.forbidden_domains:
                return True, f"domain match: '{domain}'"
        return False, ""

    def add_to_forbidden(self, company_name: str, website: str = None):
        name = company_name.strip().lower()
        _is_junk = (not name or name in ("not found", "unknown", "")
                    or name.startswith("http") or "://" in name or name.startswith("www."))
        if not _is_junk:
            self.forbidden_names.add(name)
        if website:
            domain = extract_domain(website)
            if domain:
                self.forbidden_domains.add(domain)

    def build_row(self, data: dict, status: str) -> list:
        row = []
        for header in self.headers:
            if header == "Status":
                row.append(status)
            elif header == "Date Added":
                row.append(datetime.datetime.now().strftime("%Y-%m-%d"))
            else:
                val = data.get(header, "")
                if isinstance(val, list):
                    val = ", ".join([str(i) for i in val])
                row.append(str(val) if val else "")
        return row

    def buffer_row(self, tab_name: str, data: dict, status: str):
        company = data.get("Company Name", "")
        if not company or company in ("NOT FOUND", "Unknown", ""):
            write_log(f"SKIPPED BUFFER: empty company name (was '{company}')")
            return
        row = self.build_row(data, status)
        self.session_buffer[tab_name].append(row)
        self.add_to_forbidden(company, data.get("Website"))
        write_log(f"BUFFERED: {company} -> {tab_name} [{status}]")

    def commit_session(self):
        if self._committed:
            return {}
        counts = {}
        for tab, rows in self.session_buffer.items():
            if not rows:
                continue
            try:
                self.service.spreadsheets().values().append(
                    spreadsheetId=self.sheet_id, range=f'{tab}!A1',
                    valueInputOption='USER_ENTERED', insertDataOption='INSERT_ROWS',
                    body={'values': rows}).execute()
                counts[tab] = len(rows)
                write_log(f"COMMITTED: {len(rows)} rows to '{tab}'")
            except Exception as e:
                write_log(f"ERROR: Failed to commit {len(rows)} rows to '{tab}': {e}")
                counts[tab] = 0
        self._committed = True
        return counts

    def buffer_count(self):
        return sum(len(rows) for rows in self.session_buffer.values())

    def forbidden_count(self):
        return len(self.forbidden_names) + len(self.forbidden_domains)


# ============================================================
# 3. PERPLEXITY API — with retry + JSON extraction
# ============================================================
def perplexity_call(system_prompt, user_prompt, retries=PERPLEXITY_RETRIES):
    """Send a prompt to the Perplexity sonar-pro API with retry logic.

    Args:
        system_prompt: System-role message defining the model's behavior.
        user_prompt: User-role message with the actual query.
        retries: Number of retry attempts on failure (default: PERPLEXITY_RETRIES).

    Returns:
        Response content string, or None if all attempts fail.
    """
    url = "https://api.perplexity.ai/chat/completions"
    headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }
    for attempt in range(retries + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except requests.exceptions.Timeout:
            write_log(f"TIMEOUT: Perplexity attempt {attempt+1}/{retries+1}")
            if attempt < retries:
                time.sleep(3)
            continue
        except Exception as e:
            write_log(f"ERROR: Perplexity call failed (attempt {attempt+1}): {e}")
            if attempt < retries:
                time.sleep(3)
            continue
    return None


def extract_json_array(raw: str) -> list:
    if not raw:
        return []
    cleaned = raw.strip()

    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "companies" in result:
            return result["companies"]
    except json.JSONDecodeError:
        pass

    code_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', cleaned)
    if code_block:
        inner = code_block.group(1).strip()
        if inner.upper() == "NOT FOUND" or inner == "":
            return []  # Perplexity signals no results — not a parse error
        try:
            result = json.loads(inner)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    start = cleaned.find('[')
    end = cleaned.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass

    write_log(f"PARSE FAILED — raw (first 300 chars): {cleaned[:300]}")
    return []


def extract_json_object(raw: str) -> dict | None:
    if not raw:
        return None
    # Strip markdown code fences and fix unquoted NOT FOUND values
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.DOTALL)
    raw = re.sub(r':\s*NOT FOUND\b', ': "NOT FOUND"', raw)
    try:
        result = json.loads(raw.strip())
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    code_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', raw)
    if code_block:
        try:
            result = json.loads(code_block.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            cleaned_obj = re.sub(r':\s*NOT FOUND\b', ': "NOT FOUND"', raw[start:end + 1])
            return json.loads(cleaned_obj)
        except json.JSONDecodeError:
            pass

    write_log(f"PARSE FAILED — raw (first 300 chars): {raw[:300]}")
    return None


# ============================================================
# 4. HALLUCINATION FILTER — Handled via prompt, not regex
# ============================================================
# Hallucination prevention is done in the Discovery prompt itself:
# Perplexity is instructed to only return companies with a verified website.
# The post-verification check (v_name == "not found") is the safety net.
# Regex-based name filtering was removed because many real German SMEs
# have generic-sounding names (e.g. "Industrieservice Nord GmbH" can be real).


# ============================================================
# 5. DISCOVERY — Search-oriented, not list-from-memory
# ============================================================
# Rotating search archetypes to get diverse results
SEARCH_ARCHETYPES: list = []   # dynamically set from _active_prompts in run_ma_agent_loop


def discover_companies(industry: str, state: SheetState, batch_num: int, region: str = "") -> list[dict]:
    """Search-oriented discovery — asks Perplexity to SEARCH, not to list from memory.

    Args:
        industry: Target industry/niche string (e.g. "Industriemontage").
        state: Active SheetState used for forbidden-name deduplication.
        batch_num: Current batch index; used to rotate SEARCH_ARCHETYPES.

    Returns:
        List of dicts with at minimum {"company_name": str, "website": str}.
    """
    # Rotate archetype based on batch number, resolve {industry} placeholder
    _archetypes = _active_prompts["discovery"]["search_archetypes"]
    archetype = render_template(
        _archetypes[batch_num % len(_archetypes)],
        industry=industry,
        region=region,
    )

    _disc = _active_prompts["discovery"]
    system_prompt = render_template(_disc["system"], region=region)
    user_prompt = render_template(
        _disc["user_template"],
        archetype=archetype,
        discovery_count=str(DISCOVERY_COUNT),
        discovery_extra="",
        region=region,
    )
    raw = perplexity_call(system_prompt, user_prompt)
    if not raw:
        return []

    items = extract_json_array(raw)
    candidates = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = item.get("name", item.get("company_name", item.get("company", "")))
        else:
            continue

        if not name or name.strip().lower() in ("not found", "unknown", "n/a", "keine angabe"):
            continue

        if isinstance(item, dict):
            item["name"] = name
            candidates.append(item)
        else:
            candidates.append({"name": name})

    write_log(f"Discovery returned {len(candidates)} candidates (after hallucination filter)")
    return candidates


# ============================================================
# 6. VERIFICATION — M&A Expert with Impressum + LinkedIn priority
# ============================================================
def verify_company(company_name: str, industry: str, region: str = "", website: str = "") -> dict | None:
    """Verify a candidate company via Perplexity and return structured M&A data.

    Checks ownership structure, revenue, employee count, CEO/contact, and
    Impressum details. Flags Tochtergesellschaft status and parent revenue.

    Args:
        company_name: Full legal name of the company.
        industry: Target industry/niche for context in the prompt.

    Returns:
        Dict of verified fields, or None if the API call fails.
    """
    roles_str = ", ".join(CRITERIA.required_roles) if CRITERIA.required_roles else "Geschäftsführer, CEO, Inhaber"
    rev_label = CRITERIA.revenue_label()
    emp_label = CRITERIA.employee_label()

    _ver = _active_prompts["verify"]
    system_prompt = _ver["system"]
    user_prompt = render_template(
        _ver["user_template"],
        company_name=company_name,
        industry=industry,
        rev_label=rev_label,
        emp_label=emp_label,
        roles_str=roles_str,
        buyer_profile_extra="",
        verify_extra="",
        region=region,
        website=website,
    )
    raw = perplexity_call(system_prompt, user_prompt)
    if not raw:
        return None

    result = extract_json_object(raw)
    if not result:
        write_log(f"ERROR: Could not parse verification for {company_name}")
        return None

    # --- Normalize field names (new standardized → old pipeline keys) ---
    if "revenue_eur" in result and "revenue" not in result:
        result["revenue"] = result["revenue_eur"]
    if "employees_count" in result and "employees" not in result:
        result["employees"] = result["employees_count"]

    return result


# ============================================================
# 7. HARD GATES — Programmatic checks with Rev/FTE triangulation
# ============================================================
def parse_revenue(revenue_str) -> float | None:
    """Parse revenue from various German/English formats into a EUR float.

    Handles: 8500000, "8.5 Mio EUR", "~8.5M", "8,5 Million EUR",
    "ca. 8.500.000 EUR", "8.5 Mio. €", German thousands dots, etc.
    Guards against Mrd/Billion confusion.

    Args:
        revenue_str: Raw revenue string or numeric value from Perplexity output.

    Returns:
        Revenue as a float in EUR, or None if unparseable/missing.
    """
    if not revenue_str or str(revenue_str) in ("NOT FOUND", "UNVERIFIED", "N/A", "not found"):
        return None

    # If it's already a number (from standardized JSON), use directly
    if isinstance(revenue_str, (int, float)):
        val = float(revenue_str)
        # Sanity: if value > 1 billion, it's likely a data error
        if val > 1_000_000_000:
            write_log(f"REVENUE SANITY: {val} > 1B — likely Mrd/M confusion, dividing by 1000")
            val = val / 1000
        return val if val > 0 else None

    s = str(revenue_str).lower().strip()
    s = s.replace("€", "").replace("eur", "").replace("~", "").replace("ca.", "")
    s = s.replace("(estimated)", "").replace("(geschätzt)", "").strip()

    # Check for Milliarden/Billion (should never match for SMEs)
    mrd_match = re.search(r'([\d.,]+)\s*(milliard|mrd|billion|b\b)', s)
    if mrd_match:
        write_log(f"REVENUE SANITY: Found 'Mrd/Billion' in '{revenue_str}' — likely not an SME")
        return float(mrd_match.group(1).replace(",", ".").replace("..", ".")) * 1_000_000_000

    # Million / Mio / M
    mio_match = re.search(r'([\d.,]+)\s*(million|mio\.?|m\b)', s)
    if mio_match:
        num_str = mio_match.group(1)
        # Handle German number format: "8,5" or "8.5"
        if "," in num_str and "." not in num_str:
            num_str = num_str.replace(",", ".")
        elif "," in num_str and "." in num_str:
            # "8.500,00" → German thousands separator
            num_str = num_str.replace(".", "").replace(",", ".")
        return float(num_str) * 1_000_000

    # Tausend / T / k
    t_match = re.search(r'([\d.,]+)\s*(tausend|tsd|t\b|k\b)', s)
    if t_match:
        num_str = t_match.group(1).replace(",", ".").replace("..", ".")
        return float(num_str) * 1_000

    # Plain number — handle German formatting "8.500.000" vs "8500000" vs "900.000"
    s_clean = s.replace(" ", "")
    # German thousands with multiple dots: "8.500.000"
    if s_clean.count(".") >= 2:
        s_clean = s_clean.replace(".", "")
    # German thousands with single dot + exactly 3 trailing digits: "900.000" or "2.850.000"
    elif re.search(r'\d+\.\d{3}(?!\d)', s_clean):
        s_clean = re.sub(r'\.(\d{3})(?!\d)', r'\1', s_clean)
    elif "," in s_clean and "." in s_clean:
        s_clean = s_clean.replace(".", "").replace(",", ".")
    elif "," in s_clean:
        s_clean = s_clean.replace(",", ".")

    match = re.search(r'([\d.]+)', s_clean)
    if match:
        val = float(match.group(1))
        # Heuristic: values under 1000 are likely in millions
        if val < 1000:
            val = val * 1_000_000
        # Sanity: > 1 billion is almost certainly wrong for an SME
        if val > 1_000_000_000:
            write_log(f"REVENUE SANITY: parsed {val} from '{revenue_str}' — dividing by 1000")
            val = val / 1000
        return val
    return None


def parse_employees(emp_str) -> int | None:
    if not emp_str or str(emp_str) in ("NOT FOUND", "UNVERIFIED", "N/A", "not found"):
        return None
    # If it's already a number
    if isinstance(emp_str, (int, float)):
        return int(emp_str) if emp_str > 0 else None
    s = str(emp_str).replace("~", "").replace("ca.", "").replace("(estimated)", "").replace("(geschätzt)", "").strip()
    # Handle ranges like "50-80" → take first number
    match = re.search(r'(\d+)', s)
    return int(match.group(1)) if match else None


def _is_missing(value) -> bool:
    return value in (None, "", "NOT FOUND", "UNVERIFIED", "not found", "N/A", "n/a")


def preflight_check(data: dict, company_name: str, industry: str, region: str = "") -> tuple[dict, bool]:
    """Fact-check implausible Rev/FTE ratios via a targeted Perplexity call.

    Only fires when the ratio is outside the 30k–500k EUR/FTE range.
    Overwrites revenue and employee fields in data if better values are found.

    Args:
        data: Verified company dict (mutated in-place if a call is made).
        company_name: Full legal name used in the follow-up prompt.
        industry: Target industry for context.

    Returns:
        Tuple of (updated data dict, did_call_api bool).
    """
    revenue = parse_revenue(data.get("revenue", data.get("revenue_eur")))
    employees = parse_employees(data.get("employees", data.get("employees_count")))

    if not revenue or not employees or employees == 0:
        return data, False

    ratio = revenue / employees
    if 30_000 <= ratio <= 500_000:
        return data, False  # Ratio is plausible, no fact-check needed

    write_log(f"PRE-FLIGHT: {company_name} Rev/FTE={ratio:,.0f}€ is suspicious — requesting fact-check")
    print(f"    [PRE-FLIGHT] Rev/FTE={ratio:,.0f}€ suspicious — fact-checking...")

    _pre = _active_prompts["preflight"]
    raw = perplexity_call(
        _pre["system"],
        render_template(
            _pre["user_template"],
            company_name=company_name,
            industry=industry,
            revenue_eur=str(data.get("revenue", data.get("revenue_eur"))),
            employees_count=str(data.get("employees", data.get("employees_count"))),
            region=region,
        ),
    )
    if raw:
        correction = extract_json_object(raw)
        if correction:
            note = correction.get("correction_note", "")
            new_rev = correction.get("revenue_eur")
            new_emp = correction.get("employees_count")
            if new_rev and isinstance(new_rev, (int, float)) and new_rev > 0:
                data["revenue"] = new_rev
                data["revenue_eur"] = new_rev
                data["revenue_source"] = "fact-check"
            if new_emp and isinstance(new_emp, (int, float)) and new_emp > 0:
                data["employees"] = int(new_emp)
                data["employees_count"] = int(new_emp)
                data["employees_source"] = "fact-check"
            write_log(f"PRE-FLIGHT CORRECTION: {company_name} | {note}")
            print(f"    [PRE-FLIGHT] Corrected: {note}")
    return data, True


def hard_gate_check(data: dict, criteria: TargetCriteria = CRITERIA) -> tuple[bool, str, str]:
    """Run deterministic hard gates against TargetCriteria thresholds.

    Checks ownership, revenue range, employee range, and missing-data flags.
    Any gate with a threshold of 0 is skipped (disabled via .env).

    Args:
        data: Verified company dict with parsed revenue/employee/ownership fields.
        criteria: TargetCriteria instance (defaults to global CRITERIA from .env).

    Returns:
        Tuple of (passed: bool, reason: str, destination: str).
        destination is one of: "Abgelehnt", "Needs Research", "Ready to Call".
    """
    revenue = parse_revenue(data.get("revenue", data.get("revenue_eur")))
    employees = parse_employees(data.get("employees", data.get("employees_count")))
    ownership = str(data.get("ownership_type", "")).lower().strip()

    # --- Ghost SME check: subsidiary with large parent ---
    parent_rev = data.get("parent_revenue_eur", 0)
    parent_name = data.get("parent_company", "none")
    if isinstance(parent_rev, (int, float)) and parent_rev > 100_000_000:
        return False, (f"Ghost SME: parent '{parent_name}' has {parent_rev/1e6:.0f}M EUR revenue"
                       ), "Abgelehnt"
    if parent_name and str(parent_name).lower() not in ("none", "not found", "", "n/a"):
        if ownership in ("subsidiary", "group", "public", "konzern", "tochter", "listed"):
            return False, f"Subsidiary of {parent_name} (ownership: {ownership})", "Abgelehnt"

    # --- HARD REJECTIONS ---
    if revenue is not None:
        if criteria.rev_min and revenue < criteria.rev_min:
            return False, f"Revenue {revenue:,.0f} EUR < min {criteria.rev_min:,.0f}", "Abgelehnt"
        if criteria.rev_max and revenue > criteria.rev_max:
            return False, f"Revenue {revenue:,.0f} EUR > max {criteria.rev_max:,.0f}", "Abgelehnt"

    if employees is not None:
        if criteria.emp_min and employees < criteria.emp_min:
            return False, f"Employees {employees} < min {criteria.emp_min}", "Abgelehnt"
        if criteria.emp_max and employees > criteria.emp_max:
            return False, f"Employees {employees} > max {criteria.emp_max}", "Abgelehnt"

    # Rev/FTE triangulation
    if revenue and employees and employees > 0:
        ratio = revenue / employees
        if criteria.rev_per_emp_min and ratio < criteria.rev_per_emp_min:
            return False, f"Rev/FTE {ratio:,.0f}€ below {criteria.rev_per_emp_min:,.0f}€ threshold", "Abgelehnt"
        if criteria.rev_per_emp_max and ratio > criteria.rev_per_emp_max:
            return False, f"Rev/FTE {ratio:,.0f}€ above {criteria.rev_per_emp_max:,.0f}€ threshold", "Abgelehnt"

    # Ownership gate — exact keyword match
    if ownership in ("subsidiary", "group", "public", "konzern", "tochter"):
        return False, f"Ownership: {ownership}", "Abgelehnt"

    # Check AI's fit verdict
    fit = str(data.get("fit_verdict", "")).upper()
    if "NO FIT" in fit:
        return False, f"AI verdict: {data.get('fit_verdict', 'NO FIT')}", "Abgelehnt"

    # --- SOFT GATES → Needs Research ---
    if _is_missing(data.get("ceo_name")):
        return True, "CEO not found", "Needs Research"
    if _is_missing(data.get("email")) and _is_missing(data.get("phone")):
        return True, "No contact info", "Needs Research"
    if revenue is None and employees is None:
        return True, "No financial data — needs manual check", "Needs Research"

    return True, "All checks passed", "Ready to Call"


# ============================================================
# 8. DIRECT SHEET MAPPING — No GPT, fixed keys
# ============================================================
def map_to_sheet(data: dict, industry: str) -> dict:
    """Map a verified company dict to the canonical Google Sheets column layout.

    Args:
        data: Verified + gate-checked company dict from verify_company().
        industry: Target industry string written to the "Sector" column.

    Returns:
        Dict keyed by sheet column header names (ready for buffer_row).
    """
    sources = []
    for key in ("revenue_source", "employees_source", "impressum_url", "linkedin"):
        val = data.get(key, "")
        if val and val not in ("NOT FOUND", "not found"):
            sources.append(str(val))

    return {
        "Company Name": data.get("company_name", ""),
        "Country": data.get("country", "Germany"),
        "Sector/Sub-sector": industry,
        "Website": data.get("website", ""),
        "Short Description": data.get("description", ""),
        "Why Interesting": data.get("why_interesting", ""),
        "Risks": data.get("risks", ""),
        "Estimated Revenue (EUR)": data.get("revenue", ""),
        "Employee Count (Est.)": data.get("employees", ""),
        "CEO/Founder Name": data.get("ceo_name", ""),
        "CEO Email": data.get("email", ""),
        "CEO Phone": f"'{data.get('phone', '')}" if str(data.get("phone", "")).startswith("+") else data.get("phone", ""),
        "Quellen / Links": " | ".join(sources) if sources else "",
    }


def generate_sub_niches_openai(broad_industry: str) -> list[str] | None:
    """Generate 5 specific M&A sub-niches via GPT-4o-mini.

    Args:
        broad_industry: User-supplied industry string.

    Returns:
        List of 5 niche strings, or None on any failure.
    """
    if not _OPENAI_AVAILABLE or not OPENAI_API_KEY:
        return None
    client = _openai_lib.OpenAI(api_key=OPENAI_API_KEY)
    system_prompt = (
        "You are an elite M&A advisor. The user provides a broad industry. "
        "Generate 5 highly specific, highly fragmented, and profitable sub-niches "
        "for SME acquisitions in Europe. Output ONLY a valid JSON list of 5 strings. "
        "No markdown formatting like ```json, just the raw array."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": broad_industry},
            ],
            temperature=0.7,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        niches = json.loads(raw)
        if isinstance(niches, list) and len(niches) == 5:
            return [str(n) for n in niches]
        return None
    except Exception as exc:
        print(f"  [Warning] Sub-niche expansion failed: {exc}")
        return None


# ============================================================
# 9. MAIN PIPELINE — Simple, linear, no leads lost
# ============================================================
def run_ma_agent_loop():
    print("\n" + "=" * 55)
    print("  M&A COMMAND CENTER V5.2 — INVESTMENT GRADE")
    print("=" * 55)
    print(f"  Revenue: {CRITERIA.revenue_label()} | Employees: {CRITERIA.employee_label()}")
    print(f"  Rev/FTE: €{CRITERIA.rev_per_emp_min:,.0f}-{CRITERIA.rev_per_emp_max:,.0f}")

    service = authenticate_google_sheets()
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
    sheet_id = config.get('SPREADSHEET_ID') or input("Enter Spreadsheet ID: ")

    state = SheetState(service, sheet_id)

    # --- REGION SELECTION ---
    print("\nSelect Target Region:")
    print("  1. DACH    (Germany, Austria, Switzerland)")
    print("  2. UK      (United Kingdom)")
    print("  3. Benelux (Netherlands, Belgium, Luxembourg)")
    print("  OR type a custom region name (e.g. 'France', 'Nordics')")
    _region_map = {"1": "DACH", "2": "UK", "3": "Benelux"}
    while True:
        region_input = input("\nChoose 1-3 or custom name: ").strip()
        if not region_input:
            print("  Input cannot be empty. Please try again.")
            continue
        if region_input in _region_map:
            region = _region_map[region_input]
            custom_region_name = region
        elif region_input in _config["prompts"]:
            region = region_input
            custom_region_name = region
        else:
            region = "Custom"
            custom_region_name = region_input
        break
    global _active_prompts
    _active_prompts = _config["prompts"][region]
    print(f"  Region: {custom_region_name}")

    niche_map = _active_prompts["discovery"].get("niche_suggestions", {})
    niche_labels = _active_prompts["discovery"].get("niche_labels", "")
    print("\nNiche suggestions:")
    print(niche_labels)
    user_input = input("\nChoose 1-5 OR type your own: ").strip()
    target_industry = niche_map.get(user_input, user_input)
    # --- AUTO-NICHE EXPANSION ---
    if _OPENAI_AVAILABLE and OPENAI_API_KEY:
        expand = input("\nWant AI sub-niche expansion? (y/n): ").strip().lower()
        if expand == "y":
            print(f"  Generating sub-niches for '{target_industry}' via GPT-4o-mini...")
            sub_niches = generate_sub_niches_openai(target_industry)
            if sub_niches:
                print("\nSub-niche options:")
                for i, niche in enumerate(sub_niches, 1):
                    print(f"  {i}. {niche}")
                pick = input("\nChoose 1-5 OR type custom (Enter = keep original): ").strip()
                sub_map = {str(i): sub_niches[i - 1] for i in range(1, 6)}
                if pick in sub_map:
                    target_industry = sub_map[pick]
                elif pick:
                    target_industry = pick
                print(f"  Using: {target_industry}")
    while True:
        try:
            count_needed = int(input("How many 'Ready to Call' targets? "))
            if count_needed > 0:
                break
            print("  Must be a positive integer.")
        except ValueError:
            print("  Invalid input — please enter a number.")

    write_log("--- NEUE SESSION GESTARTET ---")
    write_log(f"Nische: {target_industry} | Region: {custom_region_name} | Ziel: {count_needed} | Pipeline: V5.2 | "
              f"Rev: {CRITERIA.revenue_label()} | Emp: {CRITERIA.employee_label()} | "
              f"Rev/FTE: {CRITERIA.rev_per_emp_min}-{CRITERIA.rev_per_emp_max}")

    api_costs = 0.0
    stats = {"discovered": 0, "verified": 0, "ready": 0, "needs_research": 0,
             "rejected": 0, "duplicates": 0, "hallucinations": 0}

    targets_done = 0
    consecutive_failures = 0
    smart_retry = 0
    batch_num = 0
    _niche_broadened = False

    while targets_done < count_needed:
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            write_log(f"ABORT: {MAX_CONSECUTIVE_FAILURES} consecutive failures.")
            print(f"\n  ABORTED after {MAX_CONSECUTIVE_FAILURES} failures. Committing buffer...")
            break

        # --- NICHE EXHAUSTION WARNING + AUTO-BROADENING ---
        if consecutive_failures == 3 and not _niche_broadened:
            _niche_broadened = True
            words = target_industry.split()
            if len(words) > 2:
                target_industry = " ".join(words[:-2])
                print(f"\n  *** NICHE EXHAUSTED: auto-broadening to '{target_industry}' ***")
                write_log(f"NICHE EXHAUSTED: auto-broadened query to '{target_industry}'")
            else:
                print(f"\n  *** WARNING: Niche highly exhausted. Consider broadening your search term next time. ***")
                write_log("NICHE EXHAUSTED: query too short to broaden further")
            consecutive_failures = 0
            smart_retry = 0

        batch_num += 1
        remaining = count_needed - targets_done

        print(f"\n{'='*55}")
        print(f"  BATCH {batch_num} | Need {remaining} more | "
              f"Buffered: {state.buffer_count()} | Known: {state.forbidden_count()}")
        print(f"{'='*55}")

        # --- PHASE 1: Discovery ---
        _arc_count = len(_active_prompts["discovery"]["search_archetypes"])
        print(f"\n  Discovering companies (archetype {(batch_num % _arc_count) + 1}/{_arc_count})...")
        candidates = discover_companies(target_industry, state, batch_num, region=custom_region_name)
        api_costs += COST_PERPLEXITY

        if not candidates:
            smart_retry += 1
            if smart_retry <= SMART_RETRY_MAX:
                _n = len(_active_prompts["discovery"]["search_archetypes"])
                batch_num += _n // 2
                write_log(f"Zero-candidate batch — Smart-Retry {smart_retry}/{SMART_RETRY_MAX}, jumping to archetype {batch_num % _n}")
                print(f"  Discovery returned nothing. Smart-Retry {smart_retry}/{SMART_RETRY_MAX}...")
            else:
                consecutive_failures += 1
                smart_retry = 0
                write_log(f"Zero-candidate Smart-Retry exhausted — consecutive_failures={consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")
                print("  Discovery returned nothing. Consecutive failure logged.")
            continue

        stats["discovered"] += len(candidates)
        print(f"  Found {len(candidates)} real candidates")

        # --- PHASE 2: Dedup ---
        unique = []
        for c in candidates:
            name = c.get("name", "")
            if not name:
                continue
            is_dup, reason = state.is_duplicate(name, c.get("website"))
            if is_dup:
                print(f"    SKIP (dup: {reason}): {name}")
                stats["duplicates"] += 1
            else:
                unique.append(c)
                state.add_to_forbidden(name, c.get("website"))

        if not unique:
            print("  All candidates were duplicates.")
            smart_retry += 1
            write_log(f"BATCH {batch_num}: all dupes — smart_retry={smart_retry}/{SMART_RETRY_MAX}")
            if smart_retry >= SMART_RETRY_MAX:
                consecutive_failures += 1
                smart_retry = 0
                write_log(f"Smart-Retry exhausted — consecutive_failures={consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")
            else:
                batch_num += len(_active_prompts["discovery"]["search_archetypes"]) // 2
            continue

        print(f"  {len(unique)} unique candidates to verify\n")

        # --- PHASE 3: Verify EVERY candidate ---
        # Track whether any company in this batch produced a useful result.
        # If zero useful results → this batch counts as a failure (prevents infinite loop
        # when Discovery keeps returning hallucinated names that all get rejected).
        batch_useful = 0

        for i, candidate in enumerate(unique):
            name = candidate.get("name", "Unknown")
            print(f"  [{i+1}/{len(unique)}] Verifying: {name}...")

            verified = verify_company(name, target_industry, region=custom_region_name, website=c.get("website", ""))
            api_costs += COST_PERPLEXITY
            stats["verified"] += 1

            if not verified:
                print(f"    -> FAILED (no response)")
                write_log(f"VERIFICATION FAILED: {name}")
                continue

            # Check if company was actually found
            v_name = verified.get("company_name", "")
            v_name_norm = v_name.strip().lower()
            if not v_name_norm or v_name_norm in ("not found", "unknown", "n/a", "nicht gefunden", "unbekannt"):
                print(f"    -> SKIP (company not found by verifier)")
                stats["hallucinations"] += 1
                write_log(f"HALLUCINATION: '{name}' not found by verifier")
                continue

            v_website = verified.get("website", "")

            # Post-verify domain dedup (only if verified name differs)
            if v_name.strip().lower() != name.strip().lower():
                is_dup, dup_reason = state.is_duplicate(v_name, v_website)
                if is_dup:
                    print(f"    -> SKIP (post-verify dup: {dup_reason})")
                    stats["duplicates"] += 1
                    continue

            if v_website:
                domain = extract_domain(v_website)
                if domain and domain in state.forbidden_domains:
                    print(f"    -> SKIP (domain dup: {domain})")
                    stats["duplicates"] += 1
                    continue
                state.add_to_forbidden(v_name, v_website)

            # --- RAW FINANCIAL DEBUG ---
            raw_rev = verified.get("revenue", verified.get("revenue_eur", "MISSING"))
            raw_emp = verified.get("employees", verified.get("employees_count", "MISSING"))
            raw_own = verified.get("ownership_type", "MISSING")
            raw_parent = verified.get("parent_company", "none")
            print(f"    [RAW] Rev={raw_rev} | Emp={raw_emp} | Own={raw_own} | Parent={raw_parent}")
            write_log(f"RAW FINANCIALS: {v_name} | Rev={raw_rev} | Emp={raw_emp} | Own={raw_own} | Parent={raw_parent}")

            # --- PRE-FLIGHT: Fact-check absurd Rev/FTE ratios ---
            verified, did_preflight = preflight_check(verified, v_name, target_industry, region=custom_region_name)
            if did_preflight:
                api_costs += COST_PERPLEXITY

            # --- PHASE 4: Hard Gates ---
            passed, reason, destination = hard_gate_check(verified)
            sheet_data = map_to_sheet(verified, target_industry)

            if not passed:
                ownership = verified.get("ownership_type", "?")
                rev = verified.get("revenue", "?")
                emp = verified.get("employees", "?")
                detail = f"{reason} | Own: {ownership} | Rev: {rev} | Emp: {emp}"
                print(f"    -> REJECTED: {detail}")
                sheet_data["Notes"] = detail
                state.buffer_row("Abgelehnt", sheet_data, "Rejected")
                stats["rejected"] += 1
                write_log(f"REJECTED: {v_name} | {detail}")
                batch_useful += 1  # Rejection still counts — company was real and processed

            elif destination == "Needs Research":
                print(f"    -> NEEDS RESEARCH: {reason}")
                sheet_data["Notes"] = reason
                state.buffer_row("Needs Research", sheet_data, "Needs Research")
                stats["needs_research"] += 1
                write_log(f"NEEDS RESEARCH: {v_name} | {reason}")
                batch_useful += 1

            else:
                confidence = verified.get("confidence", "?")
                print(f"    -> READY TO CALL ✓ (confidence: {confidence})")
                state.buffer_row("Targets", sheet_data, "Ready to Call")
                stats["ready"] += 1
                batch_useful += 1
                targets_done += 1
                write_log(f"READY TO CALL: {v_name} | confidence: {confidence}")

        # --- Smart-Retry: only count as failure after SMART_RETRY_MAX empty batches ---
        # A batch full of dupes/hallucinations triggers an archetype jump first.
        # Only after SMART_RETRY_MAX jumps without progress do we increment consecutive_failures.
        if batch_useful > 0:
            consecutive_failures = 0
            smart_retry = 0
        else:
            smart_retry += 1
            write_log(f"BATCH {batch_num}: zero useful results — smart_retry={smart_retry}/{SMART_RETRY_MAX}")
            print(f"  Batch produced no useful results — smart_retry: {smart_retry}/{SMART_RETRY_MAX}")
            if smart_retry >= SMART_RETRY_MAX:
                consecutive_failures += 1
                smart_retry = 0
                write_log(f"Smart-Retry exhausted — consecutive_failures={consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")
                print(f"  Smart-Retry exhausted — failure count: {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")
            else:
                # Force archetype diversity: skip ahead by half the archetype list
                _n_arc = len(_active_prompts["discovery"]["search_archetypes"])
                batch_num += _n_arc // 2
                write_log(f"Smart-Retry: jumping to archetype index {batch_num % _n_arc}")

    # --- COMMIT ---
    print(f"\n{'='*55}")
    print("  COMMITTING TO GOOGLE SHEETS...")
    print(f"{'='*55}")
    counts = state.commit_session()
    for tab, count in counts.items():
        print(f"    {tab}: {count} rows written")

    # --- SESSION SUMMARY ---
    print(f"\n{'='*55}")
    print("  SESSION COMPLETE")
    print(f"{'='*55}")
    print(f"  Ready to Call:    {stats['ready']}/{count_needed}")
    print(f"  Needs Research:   {stats['needs_research']}")
    print(f"  Rejected:         {stats['rejected']}")
    print(f"  Duplicates:       {stats['duplicates']}")
    print(f"  Hallucinations:   {stats['hallucinations']}")
    print(f"  Total Discovered: {stats['discovered']}")
    print(f"  Total Verified:   {stats['verified']}")
    print(f"  Est. API Cost:    ${api_costs:.3f}")
    print(f"{'='*55}")

    write_log(f"SESSION COMPLETE: {stats['ready']}/{count_needed} ready | "
              f"{stats['needs_research']} research | {stats['rejected']} rejected | "
              f"{stats['duplicates']} dupes | {stats['hallucinations']} hallucinations | "
              f"${api_costs:.3f}")


if __name__ == "__main__":
    run_ma_agent_loop()
