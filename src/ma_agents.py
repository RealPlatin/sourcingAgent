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
from pathlib import Path

# --- ROOT PATHS ---
_ROOT = Path(__file__).parent.parent  # src/../ → project root
_TOKEN = str(_ROOT / 'token.json')
_CREDS = str(_ROOT / 'credentials.json')

# --- ENV + API CLIENTS ---
load_dotenv(_ROOT / '.env')
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
CONFIG_FILE = str(_ROOT / 'config' / 'config.json')
LOG_FILE = str(_ROOT / 'data' / 'Session_Log.md')
DISCOVERY_COUNT = 10
MAX_CONSECUTIVE_FAILURES = 5
COST_PERPLEXITY = 0.005
PERPLEXITY_RETRIES = 2
SMART_RETRY_MAX = 2
_LANGUAGE_MAP = {"DACH": "German", "Benelux": "Dutch"}
_REASON_CEO_NOT_FOUND = "CEO not found"
_openai_client = None


def _get_openai_client():
    """Return the module-level OpenAI client, creating it once if needed."""
    global _openai_client
    if not _OPENAI_AVAILABLE or not OPENAI_API_KEY:
        return None
    if _openai_client is None:
        _openai_client = _openai_lib.OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


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

# --- Dynamic sheet tab names (configurable via config.json "sheet_tabs") ---
_sheet_tabs = _config.get("sheet_tabs", {})
TAB_TARGETS: str = _sheet_tabs.get("targets", "Targets")
TAB_DENIED: str = _sheet_tabs.get("denied", "Denied")
TAB_NEEDS_RESEARCH: str = _sheet_tabs.get("needs_research", "Needs Research")


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


def write_log(message: str) -> None:
    """Append a timestamped entry to the session log file.

    Args:
        message: Log message string to append.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {message}\n")


# ============================================================
# 2. GOOGLE SHEETS — Auth + State + Buffer
# ============================================================
def authenticate_google_sheets():
    """Authenticate with Google Sheets API using OAuth2, refreshing credentials as needed.

    On first run, opens a local browser window for OAuth consent. Persists the
    refresh token to 'token.json' for subsequent runs.

    Returns:
        Authenticated Google Sheets API service resource.
    """
    creds = None
    if os.path.exists(_TOKEN):
        creds = Credentials.from_authorized_user_file(_TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(_CREDS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(_TOKEN, 'w') as token:
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
    TABS = [TAB_TARGETS, TAB_DENIED, TAB_NEEDS_RESEARCH]

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
        self._ensure_tab(TAB_NEEDS_RESEARCH)
        self._ensure_tab(TAB_DENIED)
        self._register_interrupt_handler()

    def _load_headers(self):
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f'{TAB_TARGETS}!1:1').execute()
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

    def is_duplicate(self, company_name: str, website: str = None) -> tuple[bool, str]:
        """Check if a company already exists in the forbidden set.

        Blocks only on exact name match (case-insensitive) or exact domain match.
        Fuzzy matching is intentionally excluded to allow related-but-distinct
        companies (e.g. "Schmidt GmbH" vs "Schmidt GmbH & Co. KG") with different
        domains to both pass through.

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


def discover_companies(industry: str, localized_industry: str, state: SheetState, batch_num: int, region: str = "") -> list[dict]:
    """Search-oriented discovery — asks Perplexity to SEARCH, not to list from memory.

    Rotates through 10 search archetypes to diversify results across batches.
    The GEO-FENCE instruction in the system prompt ensures only on-region companies
    are returned.

    Args:
        industry: English industry/niche string (used for logging/verify context).
        localized_industry: Region-language-translated niche string injected into
            search archetype queries (German for DACH, Dutch for Benelux, English
            for UK/Custom).
        state: Active SheetState; used only for logging — dedup happens in the caller.
        batch_num: Internal archetype rotation index; may jump forward on Smart-Retry.
        region: Human-readable region name (e.g. "France") injected as {{region}}
            in Custom profile templates. Unused for DACH/UK/Benelux.

    Returns:
        List of candidate dicts, each with at minimum {"name": str}. May also
        include {"website": str, "city": str} if Perplexity returns them.
    """
    # Rotate archetype based on batch number, resolve {industry} placeholder with localized term
    _archetypes = _active_prompts["discovery"]["search_archetypes"]
    archetype = render_template(
        _archetypes[batch_num % len(_archetypes)],
        industry=localized_industry,
        region=region,
    )
    print(f"  DEBUG: Final Search Query: {archetype}")

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

    Executes a deep-dive prompt instructing Perplexity to: scrape Impressum/contact
    subpages, check LinkedIn for headcount, look up financials in the regional
    registry (Bundesanzeiger / Companies House / KVK), and assess ownership
    independence. Returns a normalized dict ready for hard_gate_check().

    Args:
        company_name: Full legal name of the company.
        industry: English industry/niche string for buyer-profile context.
        region: Human-readable region name (e.g. "France") for {{region}} template
            substitution. Pass `custom_region_name` from the main loop.
        website: Company website URL passed as context to the verify prompt; helps
            Perplexity target the correct Impressum page.

    Returns:
        Dict with keys: company_name, website, city, country, revenue_eur,
        revenue_source, employees_count, employees_source, ownership_type,
        parent_company, parent_revenue_eur, ceo_name, email, phone,
        impressum_url, impressum_quote, linkedin, description, why_interesting,
        risks, fit_verdict, confidence. Also aliased as revenue/employees for
        backward compatibility. Returns None if the API call fails entirely.
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


def lookup_dach_ceo(company_name: str, website: str) -> str | None:
    """Second-pass Perplexity call to find CEO/Geschäftsführer from DACH Impressum.

    Args:
        company_name: Legal company name.
        website: Company website URL for targeted search.

    Returns:
        CEO name string, or None if not found.
    """
    query = (
        f'Wer ist der Geschäftsführer von "{company_name}" '
        f'laut deren Impressum auf {website}? '
        f'Antworte NUR mit dem vollen Namen der Person. Kein Satz, nur Name.'
    )
    raw = perplexity_call(
        "Du bist ein Datenspezialist für deutsche Unternehmensregister. "
        "Antworte ausschließlich mit dem gesuchten Namen.",
        query,
    )
    if not raw:
        return None
    name = raw.strip().split("\n")[0].strip().rstrip(".")
    if not name or len(name) < 4:
        return None
    skip_patterns = ("not found", "nicht", "keine", "unbekannt", "konnte nicht", "?", "n/a")
    if any(p in name.lower() for p in skip_patterns):
        return None
    return name


def deep_link_contact_scan(website: str, company_name: str) -> dict:
    """Second-pass contact retrieval: explicitly checks /kontakt, /impressum, /uber-uns.

    Args:
        website: Base URL of the company website.
        company_name: Company name for logging.

    Returns:
        Dict with keys 'phone' and 'email' (values may be 'NOT FOUND').
    """
    if not website or _is_missing(website):
        return {}
    system = (
        "You are a contact data extractor. Return ONLY valid JSON with exactly two keys: "
        '{"phone": "...", "email": "..."}. Use "NOT FOUND" if unavailable.'
    )
    user = (
        f"Visit these pages of {website}: /kontakt, /impressum, /uber-uns, /contact-us, /about. "
        "Search for the terms: Tel, Telefon, Phone, Mail, @, Ansprechpartner, Zentrale. "
        "Return the main office phone number and a general contact email. "
        'Use "NOT FOUND" for any value you cannot find.'
    )
    raw = perplexity_call(system, user)
    if not raw:
        return {}
    result = extract_json_object(raw)
    write_log(f"DEEP-LINK-SCAN {company_name}: phone={result.get('phone')} email={result.get('email')}")
    return result


def external_contact_search(company_name: str, city: str) -> dict:
    """Safety-net contact search via public business directories (Maps, Yelp, North Data).

    Args:
        company_name: Full legal company name.
        city: City of the company's registered office.

    Returns:
        Dict with keys 'phone' and 'email' (values may be 'NOT FOUND').
    """
    system = (
        "You are a contact researcher. Return ONLY valid JSON with exactly two keys: "
        '{"phone": "...", "email": "..."}. Use "NOT FOUND" if unavailable.'
    )
    location = f" in {city}" if city and not _is_missing(city) else ""
    user = (
        f"Search for the official contact details of '{company_name}'{location}. "
        "Check Google Maps, Yelp, North Data, or the official national business registry. "
        "Provide the main office phone number and a general contact email (e.g. info@...). "
        'Use "NOT FOUND" for any value you cannot find.'
    )
    raw = perplexity_call(system, user)
    if not raw:
        return {}
    result = extract_json_object(raw)
    write_log(f"EXTERNAL-SEARCH {company_name}: phone={result.get('phone')} email={result.get('email')}")
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
        destination is one of: "Denied", "Needs Research", "Ready to Call".
    """
    revenue = parse_revenue(data.get("revenue", data.get("revenue_eur")))
    employees = parse_employees(data.get("employees", data.get("employees_count")))
    ownership = str(data.get("ownership_type", "")).lower().strip()

    # --- Ghost SME check: subsidiary with large parent ---
    parent_rev = data.get("parent_revenue_eur", 0)
    parent_name = data.get("parent_company", "none")
    if isinstance(parent_rev, (int, float)) and parent_rev > 100_000_000:
        return False, (f"Ghost SME: parent '{parent_name}' has {parent_rev/1e6:.0f}M EUR revenue"
                       ), TAB_DENIED
    if parent_name and str(parent_name).lower() not in ("none", "not found", "", "n/a"):
        if ownership in ("subsidiary", "group", "public", "konzern", "tochter", "listed"):
            return False, f"Subsidiary of {parent_name} (ownership: {ownership})", TAB_DENIED

    # --- HARD REJECTIONS ---
    if revenue is not None:
        if criteria.rev_min and revenue < criteria.rev_min:
            return False, f"Revenue {revenue:,.0f} EUR < min {criteria.rev_min:,.0f}", TAB_DENIED
        if criteria.rev_max and revenue > criteria.rev_max:
            return False, f"Revenue {revenue:,.0f} EUR > max {criteria.rev_max:,.0f}", TAB_DENIED

    if employees is not None:
        if criteria.emp_min and employees < criteria.emp_min:
            return False, f"Employees {employees} < min {criteria.emp_min}", TAB_DENIED
        if criteria.emp_max and employees > criteria.emp_max:
            return False, f"Employees {employees} > max {criteria.emp_max}", TAB_DENIED

    # Rev/FTE triangulation
    if revenue and employees and employees > 0:
        ratio = revenue / employees
        if criteria.rev_per_emp_min and ratio < criteria.rev_per_emp_min:
            return False, f"Rev/FTE {ratio:,.0f}€ below {criteria.rev_per_emp_min:,.0f}€ threshold", TAB_DENIED
        if criteria.rev_per_emp_max and ratio > criteria.rev_per_emp_max:
            return False, f"Rev/FTE {ratio:,.0f}€ above {criteria.rev_per_emp_max:,.0f}€ threshold", TAB_DENIED

    # Ownership gate — exact keyword match
    if ownership in ("subsidiary", "group", "public", "konzern", "tochter"):
        return False, f"Ownership: {ownership}", TAB_DENIED

    # Check AI's fit verdict
    fit = str(data.get("fit_verdict", "")).upper()
    if "NO FIT" in fit:
        return False, f"AI verdict: {data.get('fit_verdict', 'NO FIT')}", TAB_DENIED

    # --- SOFT GATES → Needs Research ---
    if _is_missing(data.get("ceo_name")):
        return True, _REASON_CEO_NOT_FOUND, TAB_NEEDS_RESEARCH
    if _is_missing(data.get("email")) and _is_missing(data.get("phone")):
        return True, "No contact info", TAB_NEEDS_RESEARCH
    if revenue is None and employees is None:
        return True, "No financial data — needs manual check", TAB_NEEDS_RESEARCH

    return True, "All checks passed", "Ready to Call"


# ============================================================
# 8. DIRECT SHEET MAPPING — No GPT, fixed keys
# ============================================================
def format_revenue_millions(value) -> str:
    """Format a raw revenue integer/float as a human-readable millions string.

    Args:
        value: Revenue as int, float, or string (e.g. 4500000, 'NOT FOUND').

    Returns:
        Formatted string (e.g. '4.5M', '12.0M') or the original value as-is
        if not numeric.
    """
    if value is None or str(value).strip() in ("", "NOT FOUND", "not found", "UNVERIFIED", "N/A"):
        return "NOT FOUND"
    try:
        num = float(value)
        if num <= 0:
            return "NOT FOUND"
        return f"{num / 1_000_000:.1f}M"
    except (ValueError, TypeError):
        return str(value)


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
        "Estimated Revenue (EUR)": format_revenue_millions(data.get("revenue", data.get("revenue_eur"))),
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
    client = _get_openai_client()
    if client is None:
        return None
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


def translate_industry(industry: str, region: str, custom_region_name: str) -> str:
    """Translate the industry/niche string into the primary language of the target region.

    Args:
        industry: English industry/niche string entered by the user.
        region: Profile key ('DACH', 'UK', 'Benelux', 'Custom').
        custom_region_name: Human-readable region name (e.g. 'France').

    Returns:
        Translated industry string, or the original if translation is unavailable.
    """
    if region == "UK":
        return industry
    client = _get_openai_client()
    if client is None:
        return industry
    target_lang = _LANGUAGE_MAP.get(region, f"the primary language of {custom_region_name}")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    f"Translate the following business/industry term into {target_lang}. "
                    "Return ONLY the translated term — no explanation, no quotes, no punctuation."
                )},
                {"role": "user", "content": industry},
            ],
            temperature=0.1,
            max_tokens=50,
        )
        translated = response.choices[0].message.content.strip()
        # Sanitize: strip trailing hyphens/dashes, enforce minimum 3 chars
        translated = translated.rstrip("-–—").strip()
        if len(translated) < 3:
            return industry
        return translated if translated else industry
    except Exception as exc:
        print(f"  [Warning] Translation failed: {exc}")
        return industry


def broaden_industry_gpt(industry: str, region: str) -> str:
    """Use GPT-4o-mini to find a semantically broader parent industry term.

    Falls back to stripping last 2 words if OpenAI is unavailable.

    Args:
        industry: Current (possibly too narrow) industry string.
        region: Profile key ('DACH', 'UK', 'Benelux', 'Custom').

    Returns:
        Broader industry term in the same language as the input.
    """
    words = industry.split()
    fallback = " ".join(words[:-2]).rstrip("-–—").strip() if len(words) > 2 else industry

    client = _get_openai_client()
    if client is None:
        return fallback
    target_lang = _LANGUAGE_MAP.get(region, "English")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    f"You are an industry taxonomy expert. Return a broader parent industry term in {target_lang} "
                    f"with one descriptive B2B search angle appended (e.g. 'contract manufacturing', "
                    f"'outsourcing partners', 'industrial testing', 'technical services'). "
                    f"Stay technical and within the production/technology sector. "
                    f"Never return terms as broad as 'Dienstleistung', 'Handel', 'services', or 'trade'. "
                    f"Return ONLY the term — no explanation, no quotes, no punctuation."
                )},
                {"role": "user", "content": f"Broader term with B2B angle for: '{industry}'"},
            ],
            temperature=0.1,
            max_tokens=50,
        )
        result = response.choices[0].message.content.strip().rstrip("-–—").strip()
        if len(result) >= 3 and result.lower() != industry.lower():
            return result
    except Exception as exc:
        print(f"  [Warning] GPT broadening failed: {exc}")
    return fallback


# ============================================================
# 9. MAIN PIPELINE — Simple, linear, no leads lost
# ============================================================
def run_ma_agent_loop() -> None:
    """Entry point: interactive M&A sourcing session from CLI prompt to Sheets commit.

    Orchestrates the full pipeline loop:
    1. Region + niche selection (with optional GPT-4o-mini sub-niche expansion)
    2. Translation bridge (English → German/Dutch via GPT-4o-mini)
    3. Batch discovery loop with Smart-Retry and Early Niche Pivot
    4. Per-company: Verify → Contact-Strike → Pre-Flight → Hard Gates → CEO Fallback
    5. Batch-write all results to Google Sheets (Targets / Needs Research / Denied)

    Handles Ctrl+C gracefully by committing the session buffer before exit.
    """
    print("\n" + "=" * 55)
    print("  M&A COMMAND CENTER V5.9 — INVESTMENT GRADE")
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
    # --- TRANSLATION BRIDGE ---
    localized_industry = translate_industry(target_industry, region, custom_region_name)
    if localized_industry != target_industry:
        print(f"  Search term localized: '{target_industry}' → '{localized_industry}'")

    while True:
        try:
            count_needed = int(input("How many 'Ready to Call' targets? "))
            if count_needed > 0:
                break
            print("  Must be a positive integer.")
        except ValueError:
            print("  Invalid input — please enter a number.")

    write_log("--- NEUE SESSION GESTARTET ---")
    write_log(f"Nische: {target_industry} | Lokalisiert: {localized_industry} | Region: {custom_region_name} | Ziel: {count_needed} | Pipeline: V5.9 |"
              f"Rev: {CRITERIA.revenue_label()} | Emp: {CRITERIA.employee_label()} | "
              f"Rev/FTE: {CRITERIA.rev_per_emp_min}-{CRITERIA.rev_per_emp_max}")

    api_costs = 0.0
    stats = {"discovered": 0, "verified": 0, "ready": 0, "needs_research": 0,
             "rejected": 0, "duplicates": 0, "hallucinations": 0}

    targets_done = 0
    consecutive_failures = 0
    smart_retry = 0
    batch_num = 0
    display_batch = 0
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
                target_industry = broaden_industry_gpt(target_industry, region)
                if len(target_industry) < 3:
                    print(f"\n  *** WARNING: Broadened term too short. Keeping original. ***")
                    write_log("NICHE BROADENING: result too short, skipped")
                    target_industry = " ".join(words)
                else:
                    localized_industry = translate_industry(target_industry, region, custom_region_name)
                    print(f"\n  *** NICHE EXHAUSTED: auto-broadening to '{target_industry}' (localized: '{localized_industry}') ***")
                    write_log(f"NICHE EXHAUSTED: auto-broadened query to '{target_industry}' / localized: '{localized_industry}'")
            else:
                print(f"\n  *** WARNING: Niche highly exhausted. Consider broadening your search term next time. ***")
                write_log("NICHE EXHAUSTED: query too short to broaden further")
            consecutive_failures = 0
            smart_retry = 0

        batch_num += 1
        display_batch += 1
        remaining = count_needed - targets_done

        print(f"\n{'='*55}")
        print(f"  BATCH {display_batch} | Need {remaining} more | "
              f"Buffered: {state.buffer_count()} | Known: {state.forbidden_count()}")
        print(f"{'='*55}")

        # --- PHASE 1: Discovery ---
        _arc_count = len(_active_prompts["discovery"]["search_archetypes"])
        print(f"\n  Discovering companies (archetype {(batch_num % _arc_count) + 1}/{_arc_count})...")
        candidates = discover_companies(target_industry, localized_industry, state, batch_num, region=custom_region_name)
        api_costs += COST_PERPLEXITY

        if not candidates:
            _cur_arc = (batch_num % _arc_count) + 1
            smart_retry += 1
            if smart_retry <= SMART_RETRY_MAX:
                _n = len(_active_prompts["discovery"]["search_archetypes"])
                batch_num += _n // 2
                write_log(f"Skipping Archetype {_cur_arc} — Zero Candidates (Smart-Retry {smart_retry}/{SMART_RETRY_MAX})")
                print(f"  LOG: Skipping Archetype {_cur_arc} — Niche Exhausted (Smart-Retry {smart_retry}/{SMART_RETRY_MAX})")
            else:
                consecutive_failures += 1
                smart_retry = 0
                write_log(f"Zero-candidate Smart-Retry exhausted — consecutive_failures={consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")
                print("  Discovery returned nothing. Consecutive failure logged.")
            continue

        stats["discovered"] += len(candidates)
        print(f"  Found {len(candidates)} real candidates")

        # --- PHASE 2: Dedup ---
        # Only add NAME to seen_in_batch here — do NOT add domain to forbidden_domains.
        # Adding the website domain in Phase 2 would cause every candidate to self-block
        # in Phase 3's domain check (verified website ≈ discovery website). Domains are
        # added to forbidden only after successful verification in Phase 3.
        unique = []
        seen_in_batch: set = set()
        for c in candidates:
            name = c.get("name", "")
            if not name:
                continue
            name_lower = name.strip().lower()
            if name_lower in seen_in_batch:
                stats["duplicates"] += 1
                continue
            is_dup, reason = state.is_duplicate(name, c.get("website"))
            if is_dup:
                print(f"    SKIP (dup: {reason}): {name}")
                stats["duplicates"] += 1
            else:
                unique.append(c)
                seen_in_batch.add(name_lower)
                state.add_to_forbidden(name)  # name only — no domain yet

        if not unique:
            _arc_count_local = len(_active_prompts["discovery"]["search_archetypes"])
            _cur_arc = (batch_num % _arc_count_local) + 1
            print("  All candidates were duplicates.")
            smart_retry += 1
            write_log(f"BATCH {display_batch}: all dupes — smart_retry={smart_retry}/{SMART_RETRY_MAX}")
            if smart_retry >= SMART_RETRY_MAX:
                consecutive_failures += 1
                smart_retry = 0
                write_log(f"Smart-Retry exhausted — consecutive_failures={consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")
            else:
                batch_num += _arc_count_local // 2
                write_log(f"Skipping Archetype {_cur_arc} — All Results Known (Smart-Retry {smart_retry}/{SMART_RETRY_MAX})")
                print(f"  LOG: Skipping Archetype {_cur_arc} — All Results Known (Smart-Retry {smart_retry}/{SMART_RETRY_MAX})")
            continue

        # V5.9: Early Niche Pivot if >80% of the batch are duplicates
        _total_batch = len(candidates)
        _dup_count = _total_batch - len(unique)
        if _total_batch > 0 and _dup_count / _total_batch > 0.80 and not _niche_broadened:
            _broad = broaden_industry_gpt(target_industry, region)
            if _broad and _broad != target_industry:
                write_log(
                    f"EARLY NICHE PIVOT: {_dup_count}/{_total_batch} dupes (>80%) — "
                    f"{target_industry!r} → {_broad!r}"
                )
                print(f"  [Niche Pivot] {_dup_count}/{_total_batch} dupes (>80%) → broadening to: {_broad}")
                target_industry = _broad
                localized_industry = translate_industry(target_industry, region, custom_region_name)
                _niche_broadened = True
                consecutive_failures = 0
                smart_retry = 0
                batch_num = 0
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

            verified = verify_company(name, target_industry, region=custom_region_name, website=candidate.get("website", ""))
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

            # --- V5.9 CONTACT-STRIKE: Escalating rescue passes ---
            if _is_missing(verified.get("email")) and _is_missing(verified.get("phone")):
                write_log(f"CONTACT-STRIKE: {v_name} — running Deep-Link-Scan")
                print(f"    [Contact-Strike] Deep-link scan...")
                contact = deep_link_contact_scan(verified.get("website", v_website), v_name)
                api_costs += COST_PERPLEXITY
                if not _is_missing(contact.get("phone")):
                    verified["phone"] = contact["phone"]
                if not _is_missing(contact.get("email")):
                    verified["email"] = contact["email"]

                if _is_missing(verified.get("email")) and _is_missing(verified.get("phone")):
                    write_log(f"CONTACT-STRIKE: {v_name} — running External Safety-Net Search")
                    print(f"    [Contact-Strike] External safety-net search...")
                    city = verified.get("city", verified.get("location", ""))
                    contact2 = external_contact_search(v_name, str(city))
                    api_costs += COST_PERPLEXITY
                    if not _is_missing(contact2.get("phone")):
                        verified["phone"] = contact2["phone"]
                    if not _is_missing(contact2.get("email")):
                        verified["email"] = contact2["email"]

            # --- PRE-FLIGHT: Fact-check absurd Rev/FTE ratios ---
            verified, did_preflight = preflight_check(verified, v_name, target_industry, region=custom_region_name)
            if did_preflight:
                api_costs += COST_PERPLEXITY

            # --- PHASE 4: Hard Gates ---
            passed, reason, destination = hard_gate_check(verified)
            sheet_data = map_to_sheet(verified, target_industry)

            # --- CEO FALLBACK: DACH self-correction → generic fallback ---
            if destination == TAB_NEEDS_RESEARCH and reason == _REASON_CEO_NOT_FOUND:
                if region == "DACH":
                    write_log(f"CEO LOOKUP: second-pass Impressum search for {v_name}")
                    ceo = lookup_dach_ceo(v_name, v_website)
                    api_costs += COST_PERPLEXITY
                    if ceo:
                        print(f"    [CEO-FIX] Found via Impressum: {ceo}")
                        verified["ceo_name"] = ceo
                        sheet_data = map_to_sheet(verified, target_industry)
                        passed, reason, destination = hard_gate_check(verified)
                        write_log(f"CEO LOOKUP: found '{ceo}' for {v_name}")
                if _is_missing(verified.get("ceo_name")) and not _is_missing(verified.get("phone")):
                    verified["ceo_name"] = "To Management"
                    sheet_data = map_to_sheet(verified, target_industry)
                    destination = "Ready to Call"
                    passed = True
                    reason = "CEO fallback: To Management"
                    print(f"    [CEO-FALLBACK] Set to 'To Management'")
                    write_log(f"CEO FALLBACK applied: {v_name}")

            if not passed:
                ownership = verified.get("ownership_type", "?")
                rev = verified.get("revenue", "?")
                emp = verified.get("employees", "?")
                detail = f"{reason} | Own: {ownership} | Rev: {rev} | Emp: {emp}"
                print(f"    -> REJECTED: {detail}")
                sheet_data["Notes"] = detail
                state.buffer_row(TAB_DENIED, sheet_data, "Rejected")
                stats["rejected"] += 1
                write_log(f"REJECTED: {v_name} | {detail}")
                batch_useful += 1  # Rejection still counts — company was real and processed

            elif destination == TAB_NEEDS_RESEARCH:
                print(f"    -> NEEDS RESEARCH: {reason}")
                sheet_data["Notes"] = reason
                state.buffer_row(TAB_NEEDS_RESEARCH, sheet_data, "Needs Research")
                stats["needs_research"] += 1
                write_log(f"NEEDS RESEARCH: {v_name} | {reason}")
                batch_useful += 1

            else:
                confidence = verified.get("confidence", "?")
                print(f"    -> READY TO CALL ✓ (confidence: {confidence})")
                state.buffer_row(TAB_TARGETS, sheet_data, "Ready to Call")
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
            write_log(f"BATCH {display_batch}: zero useful results — smart_retry={smart_retry}/{SMART_RETRY_MAX}")
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
                write_log(f"Smart-Retry (BATCH {display_batch}): jumping to archetype index {batch_num % _n_arc}")

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
