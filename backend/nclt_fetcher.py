"""Visible Playwright workflow for the public NCLT case-history portal.

The module deliberately stops at the portal CAPTCHA. A staff member completes
that verification in the opened Chromium window and then resumes the same
server-side browser session from Casefile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse
import json
import os
import re

from lxml import html

from database import CasefileDatabase, _from_json, _json, new_id, utc_now

try:
    from playwright.async_api import (
        Browser, BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except ImportError:  # pragma: no cover - exercised through setup-status behavior
    Browser = BrowserContext = Page = Playwright = Any  # type: ignore[misc,assignment]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment]
    async_playwright = None


ERROR_MESSAGES = {
    "BROWSER_SETUP_REQUIRED": "Playwright or its Chromium browser is not installed. Run setup_nclt_fetcher.bat once.",
    "NCLT_SITE_UNAVAILABLE": "The public NCLT website could not be reached.",
    "SEARCH_FORM_NOT_FOUND": "The expected NCLT case-number search form was not found.",
    "SEARCH_FAILED": "The NCLT search did not complete successfully.",
    "NO_CASE_FOUND": "No NCLT case matched the requested case number, year, type and bench.",
    "MULTIPLE_MATCHES_NEEDS_REVIEW": "More than one plausible NCLT case matched. Select the correct case before continuing.",
    "CASE_DETAILS_FAILED": "The selected NCLT case-details page could not be opened.",
    "PROCEEDINGS_NOT_FOUND": "Case details were found, but no proceeding-history table could be identified.",
    "ORDER_LINK_FAILED": "An order link could not be opened from the preserved NCLT browser session.",
    "DOWNLOAD_FAILED": "The NCLT order download did not complete.",
    "INVALID_PDF": "The downloaded response was empty, HTML, or not a valid PDF.",
    "MANUAL_ACTION_REQUIRED": "Manual verification required in the opened browser. Complete the CAPTCHA, then press Continue.",
    "SITE_STRUCTURE_CHANGED": "The NCLT page structure no longer matches the supported workflow. Debug evidence was saved.",
    "SESSION_EXPIRED": "The visible NCLT browser session is no longer available. Start a new check.",
}


@dataclass(frozen=True)
class NcltFetcherConfig:
    base_url: str = "https://nclt.gov.in"
    default_bench: str = "indore"
    default_case_type: str = "16"
    default_case_type_label: str = "Company Petition IB (IBC)"
    headless: bool = False
    navigation_timeout_ms: int = 45_000

    @classmethod
    def from_environment(cls) -> "NcltFetcherConfig":
        return cls(
            base_url=os.environ.get("NCLT_FETCHER_BASE_URL", "https://nclt.gov.in").rstrip("/"),
            default_bench=os.environ.get("NCLT_FETCHER_DEFAULT_BENCH", "indore"),
            default_case_type=os.environ.get("NCLT_FETCHER_DEFAULT_CASE_TYPE", "16"),
            default_case_type_label=os.environ.get("NCLT_FETCHER_DEFAULT_CASE_TYPE_LABEL", "Company Petition IB (IBC)"),
            headless=os.environ.get("NCLT_FETCHER_HEADLESS", "false").strip().lower() in {"1", "true", "yes"},
        )


@dataclass
class PortalSession:
    browser: Browser
    context: BrowserContext
    page: Page


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_component(value: Any, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", clean_text(value)).strip("_")
    return cleaned[:90] or fallback


def normalize_date(value: Any) -> Optional[str]:
    raw = clean_text(value)
    if not raw or raw in {"-", "--", "N/A", "NA"}:
        return None
    for pattern in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def source_identifier(url: str, proceeding_date: Optional[str], order_type: str, row_text: str = "") -> str:
    parsed = urlparse(url)
    for key in ("filing_no", "order_id", "document", "id"):
        match = re.search(rf"(?:[?&]|/){key}[=/]([^&#/]+)", url, re.I)
        if match:
            return f"{key}:{match.group(1)}"
    basis = "|".join((parsed.path, parsed.query, proceeding_date or "", order_type, clean_text(row_text)))
    return f"sha256:{sha256(basis.encode('utf-8')).hexdigest()}"


def case_reference_matches(text: str, case_number: str, year: int) -> bool:
    compact = clean_text(text).lower()
    number = re.escape(str(int(case_number))) if str(case_number).isdigit() else re.escape(clean_text(case_number).lower())
    return bool(re.search(rf"(?<!\d){number}(?!\d)", compact) and re.search(rf"(?<!\d){year}(?!\d)", compact))


def _table_data(document: Any, base_url: str) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    for table in document.xpath("//table"):
        headers = [clean_text(" ".join(node.itertext())) for node in table.xpath(".//thead//th")]
        rows = []
        for row in table.xpath(".//tbody/tr | .//tr[not(ancestor::thead)]"):
            cells = row.xpath("./th|./td")
            if not cells:
                continue
            values = [clean_text(" ".join(cell.itertext())) for cell in cells]
            links = []
            for anchor in row.xpath(".//a[@href]"):
                links.append({"text": clean_text(" ".join(anchor.itertext())), "url": urljoin(base_url, anchor.get("href"))})
            if any(values) or links:
                rows.append({"cells": values, "links": links, "text": clean_text(" | ".join(values))})
        if not headers and rows:
            first_cells = table.xpath(".//tr[1]/th")
            headers = [clean_text(" ".join(node.itertext())) for node in first_cells]
        tables.append({"headers": headers, "rows": rows})
    return tables


def parse_search_results(markup: str, base_url: str, case_number: str, year: int) -> List[Dict[str, Any]]:
    document = html.fromstring(markup)
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for table in _table_data(document, base_url):
        for row in table["rows"]:
            detail_links = [link for link in row["links"] if "case-detail" in link["url"].lower()]
            if not detail_links:
                continue
            link = detail_links[0]
            if link["url"] in seen:
                continue
            seen.add(link["url"])
            candidates.append({
                "id": sha256(link["url"].encode("utf-8")).hexdigest()[:16],
                "case_number": str(case_number), "year": year, "case_type": "", "bench": "",
                "case_title": row["text"], "applicant": "", "respondent": "",
                "details_url": link["url"], "row_text": row["text"],
                "exact_reference_match": case_reference_matches(row["text"], case_number, year),
            })
    return candidates


DATE_HEADERS = ("proceeding date", "hearing date", "date of hearing", "date")
NEXT_DATE_HEADERS = ("next date", "next listing date", "next hearing")
PURPOSE_HEADERS = ("purpose", "stage", "listing purpose")
STATUS_HEADERS = ("status", "case status")
ORDER_HEADERS = ("order", "orders", "order type", "interim order", "final order", "daily order", "judgment")


def _header_index(headers: Iterable[str], terms: Iterable[str], exclude: Iterable[str] = ()) -> Optional[int]:
    lowered = [clean_text(value).lower() for value in headers]
    for index, header in enumerate(lowered):
        if not any(term in header for term in exclude) and any(term in header for term in terms):
            return index
    return None


def _cell(row: Dict[str, Any], index: Optional[int]) -> str:
    return row["cells"][index] if index is not None and index < len(row["cells"]) else ""


def parse_case_details(markup: str, base_url: str) -> Dict[str, Any]:
    document = html.fromstring(markup)
    page_text = clean_text(" ".join(document.itertext()))
    pairs: Dict[str, str] = {}
    for table in _table_data(document, base_url):
        for row in table["rows"]:
            cells = row["cells"]
            for index in range(0, len(cells) - 1, 2):
                key = clean_text(cells[index]).rstrip(":").lower()
                if key and len(key) < 80:
                    pairs.setdefault(key, cells[index + 1])

    def pair(*keys: str) -> str:
        for label, value in pairs.items():
            if any(key in label for key in keys):
                return value
        return ""

    case_number = pair("case number", "case no")
    title = pair("case title", "name of parties", "party name")
    applicant = pair("petitioner", "applicant")
    respondent = pair("respondent", "corporate debtor")
    bench = pair("bench")
    status = pair("status")
    identifier = pair("filing number", "filing no", "diary number")
    if not title:
        versus = re.search(r"([A-Z][^|]{3,150}?)\s+(?:V/?S\.?|VERSUS)\s+([A-Z][^|]{3,150})", page_text, re.I)
        if versus:
            applicant, respondent = clean_text(versus.group(1)), clean_text(versus.group(2))
            title = f"{applicant} vs {respondent}"

    proceedings: List[Dict[str, Any]] = []
    for table in _table_data(document, base_url):
        headers = table["headers"]
        header_blob = " ".join(headers).lower()
        has_proceeding_signal = any(term in header_blob for term in (*DATE_HEADERS, *ORDER_HEADERS, "purpose", "next date"))
        if not has_proceeding_signal:
            continue
        date_index = _header_index(headers, DATE_HEADERS, NEXT_DATE_HEADERS)
        next_index = _header_index(headers, NEXT_DATE_HEADERS)
        purpose_index = _header_index(headers, PURPOSE_HEADERS)
        status_index = _header_index(headers, STATUS_HEADERS)
        order_index = _header_index(headers, ORDER_HEADERS)
        for row_number, row in enumerate(table["rows"], 1):
            order_links = [link for link in row["links"] if (
                ".pdf" in link["url"].lower() or "order" in (link["text"] + link["url"]).lower()
                or "judg" in (link["text"] + link["url"]).lower()
            )]
            order_text = _cell(row, order_index)
            if not order_links and not any(term in order_text.lower() for term in ("order", "judgment")):
                order_type = ""
            else:
                link_text = order_links[0]["text"] if order_links else order_text
                match = re.search(r"(Interim Order|Final Order|Daily Order|Judg(?:e)?ment|Order)", link_text, re.I)
                order_type = match.group(1).title() if match else "Order"
            order_url = order_links[0]["url"] if order_links else ""
            proceeding_date = normalize_date(_cell(row, date_index))
            proceedings.append({
                "row_number": row_number,
                "date": proceeding_date,
                "date_raw": _cell(row, date_index),
                "purpose": _cell(row, purpose_index),
                "next_date": normalize_date(_cell(row, next_index)),
                "next_date_raw": _cell(row, next_index),
                "status": _cell(row, status_index),
                "order_type": order_type,
                "order_text": order_text,
                "order_url": order_url,
                "source_identifier": source_identifier(order_url, proceeding_date, order_type, row["text"]) if order_url else "",
                "download_status": "NEW" if order_url else "NO_ORDER",
                "source_row": row["text"],
            })
    # Remove exact duplicates created by tables with nested header/body selectors.
    deduped: List[Dict[str, Any]] = []
    signatures: set[tuple[Any, ...]] = set()
    for item in proceedings:
        signature = (item["date_raw"], item["purpose"], item["next_date_raw"], item["order_url"], item["source_row"])
        if signature not in signatures:
            signatures.add(signature)
            deduped.append(item)
    return {
        "case": {
            "case_identifier": identifier, "case_number": case_number, "title": title,
            "applicant": applicant, "respondent": respondent, "bench": bench, "status": status,
        },
        "proceedings": deduped,
    }


class NcltPortalClient:
    SEARCH_PATH = "/case-number-wise"

    def __init__(self, config: NcltFetcherConfig):
        self.config = config
        self._playwright: Optional[Playwright] = None

    async def _engine(self) -> Playwright:
        if async_playwright is None:
            raise RuntimeError(ERROR_MESSAGES["BROWSER_SETUP_REQUIRED"])
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    async def prepare_search(self, request: Dict[str, Any]) -> PortalSession:
        engine = await self._engine()
        browser = await engine.chromium.launch(headless=self.config.headless)
        try:
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            page.set_default_timeout(self.config.navigation_timeout_ms)
            try:
                await page.goto(urljoin(self.config.base_url, self.SEARCH_PATH), wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
            except PlaywrightTimeoutError:
                # The government site often keeps secondary assets pending;
                # stable search controls are the readiness signal.
                pass
            for selector in ("#bench", "#case_type", "#case_number", "#case_year", "#txtInput"):
                await page.locator(selector).wait_for(state="visible", timeout=12_000)
            await page.locator("#bench").select_option(value=request["bench"])
            await page.locator("#case_type").select_option(value=request["case_type"])
            await page.locator("#case_number").fill(str(request["case_number"]))
            await page.locator("#case_year").select_option(value=str(request["case_year"]))
            return PortalSession(browser, context, page)
        except Exception:
            await browser.close()
            raise

    async def continue_after_verification(self, session: PortalSession, request: Dict[str, Any]) -> Dict[str, Any]:
        page = session.page
        current_url = page.url
        if "case-number-wise" in current_url:
            captcha = clean_text(await page.locator("#txtInput").input_value())
            if not captcha:
                return {"status": "MANUAL_ACTION_REQUIRED"}
            form = page.locator("#search-case-number-form")
            submit = form.get_by_role("button", name="Search")
            try:
                await submit.click()
                await page.wait_for_load_state("domcontentloaded", timeout=self.config.navigation_timeout_ms)
            except PlaywrightTimeoutError:
                pass
        markup = await page.content()
        lower = clean_text(await page.locator("body").inner_text()).lower()
        if "captcha" in lower and any(term in lower for term in ("invalid", "incorrect", "required")):
            return {"status": "MANUAL_ACTION_REQUIRED"}
        candidates = parse_search_results(markup, self.config.base_url, request["case_number"], request["case_year"])
        exact = [candidate for candidate in candidates if candidate["exact_reference_match"]]
        if len(exact) == 0:
            return {"status": "NO_CASE_FOUND", "candidates": candidates}
        if len(exact) > 1:
            return {"status": "MULTIPLE_MATCHES_NEEDS_REVIEW", "candidates": exact}
        return await self.open_candidate(session, exact[0])

    async def open_candidate(self, session: PortalSession, candidate: Dict[str, Any]) -> Dict[str, Any]:
        page = session.page
        try:
            await page.goto(candidate["details_url"], wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
        except PlaywrightTimeoutError:
            pass
        body = clean_text(await page.locator("body").inner_text())
        if "Unauthorized access or session expired" in body:
            return {"status": "CASE_DETAILS_FAILED"}
        # Current NCLT pages expose proceeding history either directly or
        # behind a semantically named tab/link. Click it only when visible.
        for label in ("Case Proceeding Details", "Listing History (Orders)", "Proceeding Details"):
            locator = page.get_by_text(label, exact=False).first
            try:
                if await locator.is_visible():
                    await locator.click()
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8_000)
                    except PlaywrightTimeoutError:
                        pass
                    break
            except Exception:
                continue
        parsed = parse_case_details(await page.content(), self.config.base_url)
        parsed.update({"status": "PROCEEDINGS_FETCHED" if parsed["proceedings"] else "PROCEEDINGS_NOT_FOUND", "candidate": candidate})
        return parsed


class NcltOrderFetcherService:
    def __init__(self, database: CasefileDatabase, data_dir: Path, config: Optional[NcltFetcherConfig] = None,
                 portal: Optional[NcltPortalClient] = None):
        self.database = database
        self.data_dir = Path(data_dir)
        self.config = config or NcltFetcherConfig.from_environment()
        self.portal = portal or NcltPortalClient(self.config)
        self.sessions: Dict[str, PortalSession] = {}
        self.debug_dir = self.data_dir / "debug" / "nclt_fetcher"
        self.standalone_dir = self.data_dir / "nclt_orders"
        self.case_files_dir = self.data_dir / "files"
        for directory in (self.debug_dir, self.standalone_dir, self.case_files_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def public_config(self) -> Dict[str, Any]:
        return {
            "base_url": self.config.base_url, "default_bench": self.config.default_bench,
            "default_case_type": self.config.default_case_type,
            "default_case_type_label": self.config.default_case_type_label,
            "headless": self.config.headless,
            "benches": [{"value": "indore", "label": "Indore Bench"}],
            "case_types": [{"value": "16", "label": "Company Petition IB (IBC)"}],
        }

    def _decode_run(self, row: Any) -> Dict[str, Any]:
        result = dict(row)
        result["result"] = _from_json(result.pop("result_json"), {})
        result["debug"] = _from_json(result.pop("debug_json"), {})
        return result

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM nclt_fetch_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            result = self._decode_run(row)
            records = connection.execute("SELECT * FROM nclt_fetch_records WHERE run_id=? ORDER BY proceeding_date DESC, first_seen_at", (run_id,)).fetchall()
            result["records"] = [dict(item) for item in records]
            return result

    def list_history(self, actor_id: str, case_id: Optional[str] = None, full_access: bool = False, limit: int = 20) -> List[Dict[str, Any]]:
        clauses, parameters = [], []
        if case_id:
            clauses.append("case_id=?")
            parameters.append(case_id)
        elif not full_access:
            clauses.append("created_by=?")
            parameters.append(actor_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(limit, 100)))
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT * FROM nclt_fetch_runs {where} ORDER BY created_at DESC LIMIT ?", parameters).fetchall()
            return [self._decode_run(row) for row in rows]

    def _audit(self, actor_id: str, event: str, run_id: str, case_id: Optional[str], after: Any = None) -> None:
        with self.database.transaction() as connection:
            self.database.audit(connection, actor_id, event, "nclt_fetch_run", run_id, case_id, after=after,
                                title=event.replace("_", " ").title())

    async def _close_session(self, run_id: str) -> None:
        """Release the per-run browser after a terminal outcome.

        A run intentionally keeps its browser context alive between CAPTCHA
        verification, result review, and downloads because NCLT download links
        can depend on that same public-site session.
        """
        session = self.sessions.pop(run_id, None)
        if not session:
            return
        for resource in (getattr(session, "context", None), getattr(session, "browser", None)):
            close = getattr(resource, "close", None)
            if close:
                try:
                    await close()
                except Exception:
                    pass

    def _create_run(self, request: Dict[str, Any], actor_id: str) -> str:
        run_id, now = new_id(), utc_now()
        with self.database.transaction() as connection:
            if request.get("case_id"):
                self.database.ensure_case(connection, request["case_id"])
            connection.execute(
                """INSERT INTO nclt_fetch_runs
                (id,case_id,case_number,case_year,case_type,case_type_label,bench,status,stage,created_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'STARTED','SEARCH_FORM',?,?,?)""",
                (run_id, request.get("case_id"), request["case_number"], request["case_year"], request["case_type"],
                 request["case_type_label"], request["bench"], actor_id, now, now),
            )
            self.database.audit(connection, actor_id, "NCLT_SEARCH_STARTED", "nclt_fetch_run", run_id,
                                request.get("case_id"), after=request, title="NCLT search started")
        return run_id

    def _update_run(self, run_id: str, status: str, stage: str, result: Optional[Dict[str, Any]] = None,
                    error_code: str = "", error_message: str = "", debug: Optional[Dict[str, Any]] = None,
                    completed: bool = False) -> Dict[str, Any]:
        now = utc_now()
        case = (result or {}).get("case", {})
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE nclt_fetch_runs SET status=?,stage=?,result_json=?,error_code=?,error_message=?,debug_json=?,
                case_title=COALESCE(NULLIF(?,''),case_title),applicant=COALESCE(NULLIF(?,''),applicant),
                respondent=COALESCE(NULLIF(?,''),respondent),portal_case_identifier=COALESCE(NULLIF(?,''),portal_case_identifier),
                updated_at=?,completed_at=? WHERE id=?""",
                (status, stage, _json(result or {}), error_code, error_message, _json(debug or {}), case.get("title", ""),
                 case.get("applicant", ""), case.get("respondent", ""), case.get("case_identifier", ""), now,
                 now if completed else None, run_id),
            )
        return self.get_run(run_id) or {}

    async def _debug(self, run_id: str, stage: str, page: Optional[Page], error: Exception | str) -> Dict[str, Any]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.debug_dir / f"{stamp}-{safe_component(run_id)}-{safe_component(stage)}"
        target.mkdir(parents=True, exist_ok=False)
        evidence = {"stage": stage, "timestamp": utc_now(), "error": clean_text(error), "url": "", "screenshot": "", "html": ""}
        if page:
            try:
                evidence["url"] = page.url
                screenshot = target / "page.png"
                await page.screenshot(path=str(screenshot), full_page=True)
                evidence["screenshot"] = str(screenshot.relative_to(self.data_dir))
                markup = target / "page.html"
                markup.write_text(await page.content(), encoding="utf-8")
                evidence["html"] = str(markup.relative_to(self.data_dir))
            except Exception as capture_error:
                evidence["capture_error"] = clean_text(capture_error)
        (target / "error.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        return evidence

    async def start(self, request: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        run_id = self._create_run(request, actor_id)
        try:
            session = await self.portal.prepare_search(request)
            self.sessions[run_id] = session
            self._audit(actor_id, "NCLT_MANUAL_ACTION_REQUIRED", run_id, request.get("case_id"))
            return self._update_run(run_id, "MANUAL_ACTION_REQUIRED", "CAPTCHA", error_code="MANUAL_ACTION_REQUIRED",
                                    error_message=ERROR_MESSAGES["MANUAL_ACTION_REQUIRED"])
        except Exception as error:
            code = "BROWSER_SETUP_REQUIRED" if "install" in clean_text(error).lower() or "executable" in clean_text(error).lower() else "SEARCH_FORM_NOT_FOUND" if "#" in clean_text(error) else "NCLT_SITE_UNAVAILABLE"
            evidence = await self._debug(run_id, "SEARCH_FORM", None, error)
            return self._update_run(run_id, "FAILED", "SEARCH_FORM", error_code=code,
                                    error_message=ERROR_MESSAGES[code], debug=evidence, completed=True)

    async def continue_run(self, run_id: str, actor_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError("NCLT fetch run not found")
        session = self.sessions.get(run_id)
        if not session:
            return self._update_run(run_id, "FAILED", "SESSION", error_code="SESSION_EXPIRED",
                                    error_message=ERROR_MESSAGES["SESSION_EXPIRED"], completed=True)
        request = {key: run[key] for key in ("case_number", "case_year", "case_type", "case_type_label", "bench", "case_id")}
        try:
            result = await self.portal.continue_after_verification(session, request)
            status = result.pop("status")
            if status == "MANUAL_ACTION_REQUIRED":
                return self._update_run(run_id, status, "CAPTCHA", result, status, ERROR_MESSAGES[status])
            if status == "MULTIPLE_MATCHES_NEEDS_REVIEW":
                self._audit(actor_id, "NCLT_MULTIPLE_MATCHES", run_id, run.get("case_id"), result)
                return self._update_run(run_id, status, "RESULT_SELECTION", result, status, ERROR_MESSAGES[status])
            if status in {"NO_CASE_FOUND", "CASE_DETAILS_FAILED", "PROCEEDINGS_NOT_FOUND"}:
                evidence = await self._debug(run_id, status, session.page, ERROR_MESSAGES[status])
                terminal = self._update_run(run_id, "FAILED", status, result, status, ERROR_MESSAGES[status], evidence, completed=True)
                await self._close_session(run_id)
                return terminal
            return self._store_result(run, result, actor_id)
        except Exception as error:
            evidence = await self._debug(run_id, "CONTINUE", session.page, error)
            terminal = self._update_run(run_id, "FAILED", "CONTINUE", error_code="SITE_STRUCTURE_CHANGED",
                                        error_message=ERROR_MESSAGES["SITE_STRUCTURE_CHANGED"], debug=evidence, completed=True)
            await self._close_session(run_id)
            return terminal

    async def select_candidate(self, run_id: str, candidate_id: str, actor_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError("NCLT fetch run not found")
        candidates = run.get("result", {}).get("candidates", [])
        candidate = next((item for item in candidates if item.get("id") == candidate_id), None)
        if not candidate:
            raise ValueError("Selected NCLT candidate is unavailable")
        session = self.sessions.get(run_id)
        if not session:
            return self._update_run(run_id, "FAILED", "SESSION", error_code="SESSION_EXPIRED",
                                    error_message=ERROR_MESSAGES["SESSION_EXPIRED"], completed=True)
        try:
            result = await self.portal.open_candidate(session, candidate)
            status = result.pop("status")
            if status == "PROCEEDINGS_FETCHED":
                return self._store_result(run, result, actor_id)
            evidence = await self._debug(run_id, status, session.page, ERROR_MESSAGES[status])
            terminal = self._update_run(run_id, "FAILED", status, result, status, ERROR_MESSAGES[status], evidence, completed=True)
            await self._close_session(run_id)
            return terminal
        except Exception as error:
            evidence = await self._debug(run_id, "RESULT_SELECTION", session.page, error)
            terminal = self._update_run(run_id, "FAILED", "RESULT_SELECTION", error_code="SITE_STRUCTURE_CHANGED",
                                        error_message=ERROR_MESSAGES["SITE_STRUCTURE_CHANGED"], debug=evidence, completed=True)
            await self._close_session(run_id)
            return terminal

    def _existing_record(self, connection: Any, run: Dict[str, Any], identifier: str, url: str) -> Optional[Any]:
        return connection.execute(
            """SELECT * FROM nclt_fetch_records WHERE bench=? AND case_type=? AND case_number=? AND case_year=?
            AND (source_identifier=? OR (source_url<>'' AND source_url=?)) ORDER BY downloaded_at DESC LIMIT 1""",
            (run["bench"], run["case_type"], run["case_number"], run["case_year"], identifier, url),
        ).fetchone()

    def _store_result(self, run: Dict[str, Any], result: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        now = utc_now()
        new_orders = already = orders = 0
        with self.database.transaction() as connection:
            for proceeding in result.get("proceedings", []):
                if not proceeding.get("order_url"):
                    proceeding["download_status"] = "NO_ORDER"
                    continue
                orders += 1
                existing = self._existing_record(connection, run, proceeding["source_identifier"], proceeding["order_url"])
                if existing:
                    proceeding["record_id"] = existing["id"]
                    proceeding["download_status"] = "ALREADY_DOWNLOADED" if existing["status"] == "DOWNLOADED" else existing["status"]
                    connection.execute("UPDATE nclt_fetch_records SET last_checked_at=?,run_id=? WHERE id=?", (now, run["id"], existing["id"]))
                    already += existing["status"] == "DOWNLOADED"
                    continue
                record_id = new_id()
                connection.execute(
                    """INSERT INTO nclt_fetch_records
                    (id,run_id,case_id,case_number,case_year,case_type,bench,case_title,proceeding_date,purpose,next_date,
                     proceeding_status,order_type,source_url,source_identifier,status,first_seen_at,last_checked_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (record_id, run["id"], run.get("case_id"), run["case_number"], run["case_year"], run["case_type"], run["bench"],
                     result.get("case", {}).get("title", ""), proceeding.get("date"), proceeding.get("purpose", ""), proceeding.get("next_date"),
                     proceeding.get("status", ""), proceeding.get("order_type", "Order"), proceeding["order_url"], proceeding["source_identifier"],
                     "NEW", now, now),
                )
                proceeding["record_id"], proceeding["download_status"] = record_id, "NEW"
                new_orders += 1
            summary = {
                "proceedings_found": len(result.get("proceedings", [])), "orders_found": orders,
                "new_orders": new_orders, "already_downloaded": already,
            }
            result["summary"] = summary
            case = result.get("case", {})
            connection.execute(
                """UPDATE nclt_fetch_runs SET status='READY_TO_DOWNLOAD',stage='PROCEEDINGS',result_json=?,case_title=?,
                applicant=?,respondent=?,portal_case_identifier=?,error_code='',error_message='',updated_at=? WHERE id=?""",
                (_json(result), case.get("title", ""), case.get("applicant", ""), case.get("respondent", ""),
                 case.get("case_identifier", ""), now, run["id"]),
            )
            self.database.audit(connection, actor_id, "NCLT_PROCEEDINGS_FETCHED", "nclt_fetch_run", run["id"], run.get("case_id"),
                                after=summary, title="NCLT proceedings fetched")
            self.database.audit(connection, actor_id, "NCLT_CASE_FOUND", "nclt_fetch_run", run["id"], run.get("case_id"),
                                after=case, title="NCLT case found")
            for proceeding in result.get("proceedings", []):
                if not proceeding.get("order_url"):
                    continue
                event = "NCLT_ORDER_ALREADY_DOWNLOADED" if proceeding.get("download_status") == "ALREADY_DOWNLOADED" else "NCLT_ORDER_FOUND"
                self.database.audit(connection, actor_id, event, "nclt_fetch_record", proceeding["record_id"], run.get("case_id"),
                                    after={"date": proceeding.get("date"), "order_type": proceeding.get("order_type"),
                                           "source_identifier": proceeding.get("source_identifier")},
                                    title=event.replace("_", " ").title())
        return self.get_run(run["id"]) or {}

    @staticmethod
    def _validate_pdf(content: bytes, content_type: str = "") -> None:
        prefix = content[:1024].lstrip().lower()
        if not content or len(content) < 100 or not prefix.startswith(b"%pdf") or b"<html" in prefix:
            raise ValueError(ERROR_MESSAGES["INVALID_PDF"])
        if content_type and "pdf" not in content_type.lower() and content_type.lower().startswith("text/"):
            raise ValueError(ERROR_MESSAGES["INVALID_PDF"])

    def get_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM nclt_fetch_records WHERE id=?", (record_id,)).fetchone()
            return dict(row) if row else None

    async def download_new(self, run_id: str, actor_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError("NCLT fetch run not found")
        records = [record for record in run.get("records", []) if record["status"] in {"NEW", "DOWNLOAD_FAILED"}]
        if not records:
            result = run.get("result", {})
            result["download_summary"] = {"downloaded": 0, "skipped": 0, "failed": 0, "failures": []}
            await self._close_session(run_id)
            return self._update_run(run_id, "COMPLETED", "DOWNLOAD_COMPLETE", result, completed=True)
        session = self.sessions.get(run_id)
        if not session:
            return self._update_run(run_id, "FAILED", "DOWNLOAD", error_code="SESSION_EXPIRED", error_message=ERROR_MESSAGES["SESSION_EXPIRED"], completed=True)
        downloaded, skipped, failed = [], [], []
        for record in records:
            destination: Optional[Path] = None
            fresh = self.get_record(record["id"])
            if not fresh or fresh["status"] == "DOWNLOADED":
                skipped.append(record["id"])
                continue
            try:
                response = await session.context.request.get(record["source_url"], timeout=self.config.navigation_timeout_ms)
                if not response.ok:
                    raise RuntimeError(f"HTTP {response.status}")
                content = await response.body()
                self._validate_pdf(content, response.headers.get("content-type", ""))
                digest = sha256(content).hexdigest()
                with self.database.connect() as connection:
                    duplicate_hash = connection.execute("SELECT id,file_path FROM nclt_fetch_records WHERE file_hash=? AND status='DOWNLOADED' LIMIT 1", (digest,)).fetchone()
                if duplicate_hash:
                    with self.database.transaction() as connection:
                        connection.execute("UPDATE nclt_fetch_records SET status='ALREADY_DOWNLOADED',file_hash=?,file_size=?,last_checked_at=? WHERE id=?", (digest, len(content), utc_now(), record["id"]))
                        self.database.audit(
                            connection, actor_id, "NCLT_ORDER_ALREADY_DOWNLOADED", "nclt_fetch_record", record["id"],
                            run.get("case_id"), after={"duplicate_record_id": duplicate_hash["id"], "file_hash": digest},
                            title="NCLT order already downloaded",
                        )
                    skipped.append(record["id"])
                    continue
                case_key = safe_component(f"CP_IB_{run['case_number']}_{run['case_year']}")
                directory = (self.case_files_dir / run["case_id"] / "nclt-orders") if run.get("case_id") else (self.standalone_dir / case_key)
                directory.mkdir(parents=True, exist_ok=True)
                date_part = record.get("proceeding_date") or "undated"
                base = safe_component(f"{date_part}_CP_IB_{run['case_number']}_{run['case_year']}_{record['order_type']}")
                destination = directory / f"{base}.pdf"
                if destination.exists() and sha256(destination.read_bytes()).hexdigest() != digest:
                    destination = directory / f"{base}_{safe_component(record['source_identifier'])[-10:]}.pdf"
                destination.write_bytes(content)
                now, document_id = utc_now(), None
                with self.database.transaction() as connection:
                    if run.get("case_id"):
                        document_id = new_id()
                        metadata = {
                            "source": "NCLT_PUBLIC_PORTAL", "proceeding_date": record.get("proceeding_date"),
                            "order_type": record.get("order_type"), "source_url": record.get("source_url"),
                            "downloaded_at": now, "nclt_fetch_record_id": record["id"], "file_hash": digest,
                        }
                        connection.execute(
                            """INSERT INTO documents
                            (id,case_id,name,category,status,storage_path,mime_type,source_type,linked_type,linked_id,metadata_json,
                             created_by,updated_by,created_at,updated_at)
                            VALUES (?,?,?,'NCLT Order','filed',?,'application/pdf','nclt_public_portal','nclt_fetch_record',?,?,?,?,?,?)""",
                            (document_id, run["case_id"], destination.name, str(destination.relative_to(self.data_dir)), record["id"],
                             _json(metadata), actor_id, actor_id, now, now),
                        )
                    connection.execute(
                        """UPDATE nclt_fetch_records SET status='DOWNLOADED',file_path=?,file_hash=?,file_size=?,document_id=?,
                        downloaded_at=?,last_checked_at=?,error_code='',error_message='' WHERE id=?""",
                        (str(destination.relative_to(self.data_dir)), digest, len(content), document_id, now, now, record["id"]),
                    )
                    self.database.audit(connection, actor_id, "NCLT_ORDER_DOWNLOADED", "nclt_fetch_record", record["id"],
                                        run.get("case_id"), after={"file_path": str(destination.relative_to(self.data_dir)), "file_hash": digest},
                                        title="NCLT order downloaded")
                downloaded.append(record["id"])
            except Exception as error:
                if destination and destination.is_file():
                    destination.unlink(missing_ok=True)
                code = "INVALID_PDF" if "valid PDF" in clean_text(error) or "empty" in clean_text(error) else "DOWNLOAD_FAILED"
                with self.database.transaction() as connection:
                    connection.execute("UPDATE nclt_fetch_records SET status='DOWNLOAD_FAILED',error_code=?,error_message=?,last_checked_at=? WHERE id=?", (code, clean_text(error), utc_now(), record["id"]))
                    self.database.audit(connection, actor_id, "NCLT_DOWNLOAD_FAILED", "nclt_fetch_record", record["id"], run.get("case_id"), after={"error": clean_text(error)}, title="NCLT order download failed")
                failed.append({"record_id": record["id"], "error_code": code, "message": ERROR_MESSAGES[code]})
        refreshed = self.get_run(run_id) or {}
        final_status = "COMPLETED" if not failed else "COMPLETED_WITH_ERRORS"
        result = refreshed.get("result", {})
        result["download_summary"] = {"downloaded": len(downloaded), "skipped": len(skipped), "failed": len(failed), "failures": failed}
        terminal = self._update_run(run_id, final_status, "DOWNLOAD_COMPLETE", result, completed=True)
        if not failed:
            await self._close_session(run_id)
        return terminal

    def get_record_file(self, record_id: str) -> Optional[Path]:
        record = self.get_record(record_id)
        if not record or not record.get("file_path"):
            return None
        candidate = (self.data_dir / record["file_path"]).resolve()
        allowed = [self.standalone_dir.resolve(), self.case_files_dir.resolve()]
        if not candidate.is_file() or not any(root == candidate or root in candidate.parents for root in allowed):
            return None
        return candidate
