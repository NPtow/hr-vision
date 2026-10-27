#!/usr/bin/env python3
"""Readonly quality/metric audit of typed research JSON or exported app packets.

Only explicitly named input directories are read, non-recursively. No collector,
network, credential, application data writer or source repair is invoked. Output
is restricted to an explicitly named report directory outside all input dirs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

VERSION = 1
AUDITOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
TRUSTED = {"officialWebsite", "employerID", "reviewedIdentity", "domainInBio", "officialChannelPost"}
CHANNELS = {"channel", "chat", "bot"}
BLOCKED = {"blocked", "unavailable", "failed", "error", "unauthorized", "captcha", "forbidden"}
QUALITY = {"fullText": 3, "partialText": 2, "snippet": 2, "metadata": 1, "unknown": 0}


def norm(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def parse_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone(timedelta(hours=3))) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def canonical_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return None
    try:
        p = urlsplit(value)
        h = (p.hostname or "").lower()
        if p.scheme not in {"http", "https"} or not h or p.username or p.password:
            return None
        h = h[4:] if h.startswith("www.") else h
        if h in {"telegram.me", "telegram.dog"}: h = "t.me"
        if h == "m.vk.com": h = "vk.com"
        if h.endswith(".linkedin.com"): h = "linkedin.com"
        path = p.path.rstrip("/")
        if h == "t.me" and path.startswith("/s/"): path = path[2:]
        # Social handles are case insensitive; arbitrary website paths are not.
        if h == "t.me":
            if path.startswith("/joinchat/"): path = "/+" + path[len("/joinchat/"):]
            if not path.startswith("/+"): path = path.lower()
        elif h in {"vk.com", "linkedin.com"}: path = path.lower()
        query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                                 if not k.lower().startswith("utm_")
                                 and k.lower() not in {"gclid", "yclid", "fbclid", "_openstat"}))
        port = p.port
        if port and port not in {80, 443}: h += ":" + str(port)
        return urlunsplit(("https", h, path, query, ""))
    except (ValueError, TypeError):
        return None


def is_search_url(value):
    try:
        p = urlsplit(value or "")
        h = (p.hostname or "").lower()
        return ((h.endswith("google.com") and p.path == "/search")
                or ("yandex." in h and p.path.rstrip("/") == "/search")
                or (h.endswith("telemetr.me") and p.path.rstrip("/") in {"/posts", "/channels"})
                or (h.endswith("hh.ru") and p.path.startswith("/search/")))
    except ValueError:
        return False


def address_key(value):
    value = norm(value)
    if value.lower().startswith("mailto:"):
        value = value[7:].split("?")[0].strip()
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        return "email:" + value.lower()
    if value.lower().startswith("tel:"):
        value = value[4:]
    if re.fullmatch(r"[+\d() .-]{7,25}", value):
        digits = re.sub(r"\D", "", value)
        if value.startswith("+7") and len(digits) != 11: return None
        if digits.startswith("8") and len(digits) > 11: return None
        if len(digits) == 11 and digits[0] == "8": digits = "7" + digits[1:]
        return "phone:" + digits if len(digits) >= 7 else None
    if re.fullmatch(r"@[A-Za-z0-9_]{5,}", value): value = "https://t.me/" + value[1:]
    try:
        parsed = urlsplit(value)
        if parsed.hostname in {"api.whatsapp.com", "web.whatsapp.com"}:
            phone = dict(parse_qsl(parsed.query)).get("phone", "").strip()
            if re.fullmatch(r"\+?\d{7,15}", phone): return "https://wa.me/" + phone.lstrip("+")
        if parsed.hostname == "wa.me":
            route = parsed.path.strip("/")
            if re.fullmatch(r"\+?\d{7,15}", route): return "https://wa.me/" + route.lstrip("+")
            if re.fullmatch(r"\+?\d+", route): return None
            value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        return None
    result = canonical_url(value)
    if result:
        p = urlsplit(result)
        if p.hostname in {"t.me", "vk.com", "linkedin.com"}:
            result = urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    return result


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.links = []; self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href: self.links.append(unescape(href))

    def handle_data(self, data):
        self.text.append(data)


def material_trusted(m):
    rel = str(m.get("relationship", ""))
    if rel in TRUSTED: return True
    low = rel.lower().replace("ё", "е")
    if any(x in low for x in ["кандидат", "не проверен", "не подтвержден", "требует проверки", "не установлена"]): return False
    if m.get("official") is True or m.get("isOfficialDomain") is True: return True
    return any(x in low for x in ["связь с компанией проверена вручную", "опубликовано школой", "подтвержденному employer id", "связана с официальным"])


def quality_of(m):
    if m.get("hasFullText") is True: return "fullText"
    q = str(m.get("quality", ""))
    if q in QUALITY: return q
    return "snippet" if "фрагмент" in q.lower() or "snippet" in q.lower() else "metadata"


class Audit:
    def __init__(self, scope, phase, start, end, as_of, receipt_dirs=()):
        self.scope = scope; self.phase = phase; self.start = start; self.end = end; self.as_of = as_of
        self.findings = []; self.files = []; self.seen = set(); self.materials = []
        self.contacts = []; self.vacancies = []; self.coverage = []; self.packet_ids = set()
        self.receipt_dirs = [Path(x).resolve() for x in receipt_dirs]; self.receipt_cache = {}
        self.contact_proofs = defaultdict(list); self.proof_sources = defaultdict(list)

    def read_proof_file(self, path):
        """Native sidecars provide exact href receipts lost in a typed UI export.

        These objects never increment metric counters or change affiliation.
        """
        try: x = json.loads(path.read_text())
        except (OSError, ValueError): return
        if not isinstance(x, dict) or x.get("companyID") not in self.scope: return
        cid = x["companyID"]
        for d in x.get("documents", []):
            if d.get("companyID", cid) == cid:
                self.proof_sources[(cid, canonical_url(d.get("url")))].append(d)
        for c in x.get("contacts", []) + x.get("channels", []):
            key = address_key(c.get("url", c.get("address", "")))
            if key: self.contact_proofs[(cid, key)].append(c)

    def contact_receipt(self, contact):
        """Read only a named receipt, never walk HTML/raw directories."""
        ident = str(contact.get("receiptID", ""))
        candidates = []
        if re.fullmatch(r"[A-Za-z0-9_-]{5,100}", ident):
            candidates += [d / (ident + suffix) for d in self.receipt_dirs for suffix in [".json", ".json.gz"]]
        for field in ["rawPath", "receiptPath"]:
            value = contact.get(field)
            if value and isinstance(value, str):
                candidates += [Path(value)] if Path(value).is_absolute() else [d / value for d in self.receipt_dirs]
        for path in candidates:
            path = path.resolve()
            if not any(d in path.parents for d in self.receipt_dirs) or not path.is_file(): continue
            if path not in self.receipt_cache:
                try:
                    data = path.read_bytes()
                    if path.suffix == ".gz": data = gzip.decompress(data)
                    raw = json.loads(data)
                    html = raw.get("html", "")
                    if not html and isinstance(raw.get("htmlPath"), str):
                        for base in [path.parent, path.parent.parent]:
                            html_path = (base / raw["htmlPath"]).resolve()
                            if not any(d in html_path.parents for d in self.receipt_dirs) or not html_path.is_file(): continue
                            blob = html_path.read_bytes()
                            if html_path.suffix == ".gz": blob = gzip.decompress(blob)
                            html = blob.decode("utf-8"); break
                    if not html and raw.get("acquisition") == "readOnlyRenderedBrowserDOM":
                        excerpt = raw.get("excerpt", "")
                        if not excerpt or hashlib.sha256(excerpt.encode()).hexdigest() != raw.get("excerptSHA256"):
                            self.issue("receipt_hash_mismatch", contact["companyId"], "Rendered excerpt receipt hash differs", file=str(path)); continue
                        self.receipt_cache[path] = dict(sourceURL=canonical_url(raw.get("url", raw.get("requestedURL"))),
                            addresses={address_key(urljoin(raw.get("url", ""), link.get("href", "").strip())) for link in raw.get("links", []) if isinstance(link, dict)},
                            text=norm(excerpt), path=str(path), qualityClass="partialText")
                        return self.receipt_cache[path] if self.receipt_cache[path]["sourceURL"] in {canonical_url(v) for v in contact["sourceURLs"]} else None
                    if not html: continue
                    claimed = raw.get("htmlSHA256")
                    if claimed and hashlib.sha256(html.encode()).hexdigest() != claimed:
                        self.issue("receipt_hash_mismatch", contact["companyId"], "Referenced HTML receipt hash differs", file=str(path)); continue
                    parser = LinkParser(); parser.feed(html)
                    self.receipt_cache[path] = dict(sourceURL=canonical_url(raw.get("url", raw.get("requestedURL"))),
                        addresses={address_key(urljoin(raw.get("url", raw.get("requestedURL", "")), v.strip())) for v in parser.links}, text=norm(" ".join(parser.text)), path=str(path), qualityClass="fullText")
                except (OSError, ValueError, EOFError): continue
            receipt = self.receipt_cache.get(path)
            if receipt and receipt["sourceURL"] in {canonical_url(v) for v in contact["sourceURLs"]}: return receipt
        return None

    def issue(self, code, company_id, message, *, severity="error", **detail):
        if self.phase == "baseline" and severity == "error": severity = "warning"
        self.findings.append(dict(severity=severity, code=code, companyId=company_id, message=message, **detail))

    def check_date(self, value, cid, field, ref, required=False):
        date = parse_date(value)
        if (value or required) and date is None:
            self.issue("invalid_date", cid, "Missing or unparseable date", field=field, reference=ref, value=value)
        if date and date > self.as_of + timedelta(days=1):
            self.issue("future_date", cid, "Date is later than audit observation", field=field, reference=ref, value=value)
        return date

    def add_material(self, raw, cid, path, *, projection=False):
        if raw.get("companyID", raw.get("companyId", cid)) != cid:
            self.issue("company_scope_mismatch", cid, "Material belongs to another company", file=path, id=raw.get("id")); return
        m = dict(raw)
        m.update(companyId=cid, file=path, canonicalURL=canonical_url(raw.get("url")),
                 qualityClass=quality_of(raw), trusted=material_trusted(raw), projection=projection)
        if not norm(raw.get("id")): self.issue("missing_material_id", cid, "Material has no stable ID", file=path)
        if not m["canonicalURL"]:
            self.issue("invalid_original_url", cid, "Material has no valid public original URL", file=path, id=raw.get("id"), severity="warning" if projection else "error")
        if is_search_url(raw.get("url")):
            self.issue("search_page_not_original", cid, "Search result page is not a publication", id=raw.get("id"), severity="warning")
        rt = self.check_date(raw.get("retrievedAt"), cid, "retrievedAt", raw.get("id"), required=True)
        pt = self.check_date(raw.get("publishedAt"), cid, "publishedAt", raw.get("id"))
        if pt and rt and pt > rt + timedelta(days=1):
            self.issue("publication_after_retrieval", cid, "Publication date is later than retrieval", id=raw.get("id"))
        if m["qualityClass"] == "fullText" and not norm(raw.get("text")):
            self.issue("empty_full_text", cid, "Full text flag has no text", id=raw.get("id"))
        if m["qualityClass"] == "fullText" and norm(raw.get("text")).endswith(("…", "...")):
            self.issue("possible_truncated_full_text", cid, "Full text ends with ellipsis; review provenance", id=raw.get("id"), severity="warning")
        if not (raw.get("source") or raw.get("provider") or raw.get("providers")):
            self.issue("missing_provider", cid, "No source/provider provenance", id=raw.get("id"))
        if not projection and m["trusted"] and raw.get("relationship") in TRUSTED and not raw.get("relationshipSourceURL"):
            self.issue("relationship_proof_missing", cid, "Trusted source lacks relationshipSourceURL", id=raw.get("id"))
        self.materials.append(m)

    def add_contact(self, raw, cid, path, *, projection=False, channel=False, retrieved=None):
        c = dict(raw)
        value = raw.get("address", raw.get("url", ""))
        c.update(companyId=cid, file=path, address=value, canonicalAddress=address_key(value), projection=projection)
        c["sourceURLs"] = raw.get("sourceURLs") or ([raw["sourceURL"]] if raw.get("sourceURL") else [])
        c["sourceQuote"] = raw.get("sourceQuote", raw.get("context", ""))
        c["purpose"] = raw.get("accountType") if raw.get("accountType") in CHANNELS else raw.get("purpose", "unknown")
        if channel and c["purpose"] not in CHANNELS: c["channelCandidate"] = True
        c["claimedPublished"] = (raw.get("affiliation") in {"Опубликовано школой", "Опубликовано компанией"} or raw.get("basis") in TRUSTED)
        if not c["canonicalAddress"]:
            self.issue("invalid_contact_address", cid, "Contact has no normalized public address", file=path, address=value)
        self.contacts.append(c)

    def add_coverage(self, raw, cid, path):
        c = dict(raw); c.update(companyId=cid, file=path)
        if not isinstance(raw.get("receivedCount", 0), int) or isinstance(raw.get("receivedCount", 0), bool) or raw.get("receivedCount", 0) < 0:
            self.issue("invalid_coverage_count", cid, "Coverage receivedCount must be a nonnegative integer", id=raw.get("id"))
        status = str(raw.get("status", "unknown"))
        if status in BLOCKED and not norm(raw.get("note")):
            self.issue("blocked_reason_missing", cid, "Blocked coverage needs an explicit reason", id=raw.get("id"))
        if status == "complete" and not (raw.get("paginationExhausted") is True and raw.get("windowStartReached") is True):
            self.issue("unsupported_complete_coverage", cid, "Complete coverage lacks exhausted-pagination and window-start evidence", id=raw.get("id"))
        if status == "noResults" and raw.get("receivedCount", 0) != 0:
            self.issue("contradictory_no_results", cid, "noResults coverage has a nonzero result count", id=raw.get("id"))
        for field in ["checkedAt", "requestedFrom", "requestedTo", "earliestPublishedAt", "latestPublishedAt"]:
            self.check_date(raw.get(field), cid, field, raw.get("id"))
        c["status"] = status; self.coverage.append(c)

    def read_file(self, path):
        try:
            data = path.read_bytes(); x = json.loads(data)
        except (OSError, ValueError) as exc:
            self.issue("unreadable_json", None, str(exc), file=str(path)); return
        if not isinstance(x, dict): return
        projection = isinstance(x.get("materials"), list) and isinstance(x.get("company"), dict)
        native = isinstance(x.get("documents"), list) and isinstance(x.get("companyID"), str)
        if not projection and not native: return
        cid = x["company"].get("id") if projection else x.get("companyID")
        if cid not in self.scope: return
        self.files.append(dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), companyId=cid,
                               format="projection" if projection else "sourceHistory" if "windowStart" in x else "publicResearch"))
        self.seen.add(cid)
        if projection:
            if cid in self.packet_ids: self.issue("duplicate_projection", cid, "More than one app packet for the same company")
            self.packet_ids.add(cid)
            for m in x["materials"]: self.add_material(m, cid, str(path), projection=True)
            for c in x.get("contacts", []): self.add_contact(c, cid, str(path), projection=True)
            hiring = x.get("hiring", {})
            material_ids = {m.get("id") for m in x["materials"]}
            for key, category in [("publications", "datedConfirmed"), ("undatedPublications", "undatedConfirmed"), ("uncertainPublications", "uncertain")]:
                for v in hiring.get(key, []):
                    vv = dict(v); vv.update(companyId=cid, category=category, projection=True, file=str(path))
                    if v.get("companyID", cid) != cid: self.issue("vacancy_company_mismatch", cid, "Hiring publication belongs to another company", id=v.get("id"))
                    if v.get("materialID") not in material_ids: self.issue("vacancy_material_missing", cid, "Hiring publication has no visible material", id=v.get("id"))
                    self.vacancies.append(vv)
            for c in hiring.get("sourceCoverage", []): self.add_coverage(c, cid, str(path))
            return
        if x.get("version") != 1: self.issue("unsupported_version", cid, "Native archive must have version 1", file=str(path))
        for m in x["documents"]:
            self.add_material(m, cid, str(path))
            if m.get("hiringDisposition") == "excluded": continue
            if m.get("kind") == "vacancy" or m.get("hiringDisposition") == "reviewedVacancy":
                v = dict(m); v.update(companyId=cid, projection=False, file=str(path)); self.vacancies.append(v)
        for c in x.get("contacts", []): self.add_contact(c, cid, str(path), retrieved=x.get("searchedAt"))
        for c in x.get("channels", []): self.add_contact(c, cid, str(path), channel=True, retrieved=x.get("searchedAt"))
        for c in x.get("coverage", []): self.add_coverage(c, cid, str(path))

    def finish(self, require_complete=False):
        missing = sorted(set(self.scope) - self.seen)
        if require_complete and missing:
            self.issue("companies_missing", None, "Scoped companies are absent from typed inputs", companyIds=missing)
        material_groups = defaultdict(list)
        id_urls = defaultdict(set)
        for m in self.materials:
            u = m["canonicalURL"]
            key = u if u and not is_search_url(u) else "non-original:" + str(m.get("id"))
            material_groups[(m["companyId"], key)].append(m)
            if m.get("id"): id_urls[(m["companyId"], m["id"])].add(key)
        for (cid, mid), urls in id_urls.items():
            if len(urls) > 1:
                self.issue("material_id_collision", cid, "A stable material ID refers to different original URLs", id=mid, urls=sorted(urls))
        chosen = {}
        by_cid_url = defaultdict(list)
        by_cid_id = {}
        for key, versions in material_groups.items():
            chosen[key] = max(versions, key=lambda m: (m["trusted"], QUALITY[m["qualityClass"]], str(m.get("retrievedAt", ""))))
            for m in versions:
                by_cid_url[(m["companyId"], m["canonicalURL"])].append(m)
                by_cid_id[(m["companyId"], m.get("id"))] = m
            dates = {str(m["publishedAt"])[:10] for m in versions if m.get("publishedAt")}
            if len(dates) > 1:
                self.issue("publication_date_conflict", key[0], "Same URL has differing publication dates", url=key[1], dates=sorted(dates), severity="warning")
        counts = {cid: Counter() for cid in self.scope}
        identities = {k: set() for k in ["materials", "fullTexts", "datedVacancies", "undatedVacancies", "uncertainVacancies", "publishedContacts", "contactCandidates", "channels", "chats", "bots", "profilesWithCompanyClaim"]}
        global_urls = set(); full_hashes = defaultdict(set)
        for (cid, key), m in chosen.items():
            row = counts[cid]; row["materials"] += 1; row[m["qualityClass"]] += 1
            row["trustedMaterials" if m["trusted"] else "candidateMaterials"] += 1
            if key.startswith("non-original:"):
                row["materialsWithoutOriginal"] += 1
            else:
                row["companyMaterialURLPairs"] += 1; global_urls.add(key)
                identities["materials"].add(cid + "|" + key)
                if m["qualityClass"] == "fullText": identities["fullTexts"].add(cid + "|" + key)
            text = norm(m.get("text"))
            if m["qualityClass"] == "fullText" and text:
                full_hashes[hashlib.sha256(text.encode()).hexdigest()].add((cid, key))
        for m in self.materials: counts[m["companyId"]]["rawMaterialObservations"] += 1

        contact_groups = defaultdict(list)
        for c in self.contacts:
            cid = c["companyId"]; counts[cid]["rawContactObservations"] += 1
            if not c["canonicalAddress"]: continue
            sources = []
            for u in c["sourceURLs"]:
                source_key = (cid, canonical_url(u))
                sources += by_cid_url.get(source_key, []) + self.proof_sources.get(source_key, [])
            for mid in c.get("materialIDs", []):
                if (cid, mid) in by_cid_id: sources.append(by_cid_id[(cid, mid)])
            quote = norm(c["sourceQuote"])
            quote_ok = bool(quote and any(quote in norm(m.get("text")) for m in sources))
            key = c["canonicalAddress"]
            # Attach only matching native proof; a sidecar never upgrades a candidate.
            for proof in self.contact_proofs.get((cid, key), []):
                proof_urls = {canonical_url(proof.get("sourceURL")), canonical_url(proof.get("profileSourceURL"))}
                if not proof_urls.intersection(canonical_url(v) for v in c["sourceURLs"]): continue
                proof_quotes = {norm(proof.get(k)) for k in ["context", "sourceQuote", "bio"]}
                if quote not in proof_quotes: continue
                for field in ["receiptID", "rawPath", "receiptPath", "linkEvidence", "personIdentityVerified"]:
                    if field in proof and field not in c: c[field] = proof[field]
            spellings = {norm(c["address"]), str(c["address"]).removeprefix("mailto:").removeprefix("tel:")}
            if key.startswith("https://t.me/"):
                handle = urlsplit(key).path.strip("/")
                if re.fullmatch(r"[A-Za-z0-9_]+", handle): spellings.update({"@" + handle, "t.me/" + handle})
            address_ok = any(any(v and v.casefold() in norm(m.get("text")).casefold() for v in spellings) for m in sources)
            if key.startswith("phone:"):
                address_ok = address_ok or any(key[6:] in re.sub(r"\D", "", str(m.get("text", ""))) for m in sources)
                if not address_ok:
                    visible_numbers = [match.group() for material in sources for match in re.finditer(r"(?<!\d)(?:\+?7|8)(?:[\s().-]*\d){10}(?!\d)", str(material.get("text", "")))]
                    address_ok = any(address_key(value) == key for value in visible_numbers)
            # A typed href claim alone is not evidence; check the referenced HTML.
            receipt = self.contact_receipt(c) if not (quote_ok and address_ok) else None
            if receipt:
                address_ok = address_ok or key in receipt["addresses"]
                quote_ok = quote_ok or bool(quote and quote in receipt["text"])
            # An icon-only href proves a published route, without a invented label/quote.
            href_only = not quote and bool(receipt and key in receipt["addresses"])
            proof_ok = (quote_ok or href_only) and address_ok
            if c["claimedPublished"] and not quote_ok and not href_only:
                self.issue("contact_quote_not_found", cid, "Published contact quote is absent from its saved source", address=c["address"], sourceURLs=c["sourceURLs"])
            if c["claimedPublished"] and not address_ok:
                self.issue("contact_address_not_proven", cid, "Published address is absent from saved text or typed link evidence", address=c["address"], sourceURLs=c["sourceURLs"])
            if c["purpose"] in CHANNELS:
                category = {"channel": "channels", "chat": "chats", "bot": "bots"}[c["purpose"]]
            elif c.get("kind") == "person" and c.get("affiliation") == "Компания указана в профиле":
                category = "profilesWithCompanyClaim"
            elif c["claimedPublished"] and proof_ok:
                category = "publishedContacts"
            else:
                category = "contactCandidates"
            if c.get("affiliation") == "Указана другая компания": category = "excludedOtherCompanyContacts"
            proof_quality = max([quality_of(s) for s in sources if quote and quote in norm(s.get("text"))]
                                + ([receipt["qualityClass"]] if receipt else []) + ["unknown"], key=lambda q: QUALITY[q])
            c.update(category=category, quoteVerified=quote_ok, addressVerified=address_ok, hrefOnlyProof=href_only, proofQuality=proof_quality)
            contact_groups[(cid, key)].append(c)
        rank = {"publishedContacts": 6, "channels": 5, "chats": 5, "bots": 5, "profilesWithCompanyClaim": 4, "contactCandidates": 1, "excludedOtherCompanyContacts": 0}
        global_contacts = set()
        for (cid, key), values in contact_groups.items():
            c = max(values, key=lambda c: rank[c["category"]]); category = c["category"]
            counts[cid][category] += 1; global_contacts.add(key)
            if category in identities: identities[category].add(cid + "|" + key)
            if c["claimedPublished"]: counts[cid]["publishedContactClaims"] += 1
            if c["quoteVerified"] and c["addressVerified"]: counts[cid]["contactsWithVerifiedQuoteAndAddress"] += 1
            if c["hrefOnlyProof"]: counts[cid]["contactsWithHrefOnlyProof"] += 1
            if category in {"channels", "chats", "bots"} and c["claimedPublished"] and c["addressVerified"] and (c["quoteVerified"] or c["hrefOnlyProof"]):
                counts[cid]["verified" + category[0].upper() + category[1:]] += 1
            if category == "publishedContacts":
                counts[cid]["publishedRoutes"] += 1
                if c["proofQuality"] in {"snippet", "partialText", "metadata", "unknown"}: counts[cid]["publishedRoutesFromFragments"] += 1
                if c.get("personIdentityVerified") is True and c.get("kind") == "person":
                    counts[cid]["namedPeopleVerified"] += 1

        vacancies = defaultdict(list)
        for v in self.vacancies:
            cid = v["companyId"]; u = canonical_url(v.get("url")); pt = self.check_date(v.get("publishedAt"), cid, "publishedAt", v.get("id"))
            if not u or is_search_url(u): self.issue("vacancy_original_missing", cid, "Vacancy has no original publication URL", id=v.get("id"))
            if v.get("projection"):
                category = v["category"]
                if category != "uncertain" and (v.get("uncertainty") or v.get("isConfirmed") is not True):
                    self.issue("vacancy_confirmation_conflict", cid, "Confirmed bucket contains uncertainty", id=v.get("id"))
                if category == "datedConfirmed" and (not pt or not self.start <= pt <= self.end):
                    self.issue("vacancy_window_mismatch", cid, "Dated counted vacancy is outside the fixed audit window or undated", id=v.get("id"))
                if category == "undatedConfirmed" and pt:
                    self.issue("undated_bucket_has_date", cid, "Undated hiring bucket contains a dated publication", id=v.get("id"))
                quote = norm(v.get("quote"))
                if quote and quote not in norm(v.get("text")) and quote not in norm(v.get("title")):
                    self.issue("vacancy_quote_not_found", cid, "Hiring quote is not in preserved text", id=v.get("id"))
            else:
                sufficient = quality_of(v) == "fullText" and bool(norm(v.get("text")))
                explicit_metadata = quality_of(v) == "metadata" and bool(norm(v.get("jobTitle")))
                confirmed = material_trusted(v) and (sufficient or explicit_metadata) and u and not is_search_url(u)
                category = "uncertain" if not confirmed else "undatedConfirmed" if not pt else "datedConfirmed" if self.start <= pt <= self.end else "outsideWindow"
                if v.get("hiringDisposition") == "reviewedVacancy" and re.match(r"(?i)^\s*(как\b|почему\b|что\b|кем\b|обзор\b)", str(v.get("jobTitle", v.get("title", "")))):
                    self.issue("educational_title_as_vacancy", cid, "Reviewed vacancy has an educational/article-style job title", id=v.get("id"), title=v.get("jobTitle", v.get("title")))
            key = u or "id:" + str(v.get("id")); vv = dict(v, category=category)
            vacancies[(cid, key)].append(vv)
        vrank = {"datedConfirmed": 4, "undatedConfirmed": 3, "outsideWindow": 2, "uncertain": 1}
        for (cid, key), values in vacancies.items():
            v = max(values, key=lambda v: vrank[v["category"]]); category = v["category"]
            metric = {"datedConfirmed": "datedVacancies", "undatedConfirmed": "undatedVacancies", "uncertain": "uncertainVacancies", "outsideWindow": "vacanciesOutsideWindow"}[category]
            counts[cid][metric] += 1
            if metric in identities: identities[metric].add(cid + "|" + key)

        coverage = {}
        for c in sorted(self.coverage, key=lambda c: str(c.get("checkedAt", ""))):
            key = (c["companyId"], c.get("id") or (c.get("source"), canonical_url(c.get("sourceURL"))))
            coverage[key] = c
        status_counts = Counter(c["status"] for c in coverage.values())
        for c in coverage.values():
            counts[c["companyId"]]["coverageChecks"] += 1
            if c["status"] in BLOCKED: counts[c["companyId"]]["blockedCoverageChecks"] += 1
        totals = Counter()
        segments = defaultdict(Counter)
        for cid, row in counts.items():
            row["companies"] = 1
            row["companiesWithFullText"] = int(row["fullText"] > 0)
            row["companiesWithDatedVacancy"] = int(row["datedVacancies"] > 0)
            row["companiesWithPublishedContact"] = int(row["publishedContacts"] > 0)
            row["companiesAttempted"] = int(cid in self.seen)
            row["companiesWithMaterials"] = int(row["materials"] > 0)
            row["companiesWithAnyData"] = int(row["materials"] > 0 or row["rawContactObservations"] > 0)
            totals.update(row)
            segments[self.scope[cid].get("catalogSegment", self.scope[cid].get("segment", "Unknown"))].update(row)
        totals["globalMaterialURLs"] = len(global_urls); totals["globalContactAddresses"] = len(global_contacts)
        totals["duplicateMaterialObservations"] = len(self.materials) - len(chosen)
        totals["duplicateContactObservations"] = len(self.contacts) - len(contact_groups)
        totals["duplicateVacancyObservations"] = len(self.vacancies) - len(vacancies)
        for key in ["datedVacancies", "undatedVacancies", "uncertainVacancies", "vacanciesOutsideWindow", "publishedContacts",
                    "publishedRoutes", "namedPeopleVerified", "profilesWithCompanyClaim", "contactCandidates", "channels", "chats", "bots",
                    "verifiedChannels", "verifiedChats", "verifiedBots", "fullText", "partialText", "snippet", "metadata", "unknown"]:
            totals.setdefault(key, 0)
        for f in self.files:
            if hashlib.sha256(Path(f["path"]).read_bytes()).hexdigest() != f["sha256"]:
                self.issue("input_changed_during_audit", f["companyId"], "Typed input changed while the audit was running", file=f["path"])
        duplicates = [dict(sha256=h, occurrences=[dict(companyId=c, url=u) for c, u in sorted(items)])
                      for h, items in full_hashes.items() if len(items) > 1]
        return dict(version=VERSION, auditorSHA256=AUDITOR_SHA256, phase=self.phase, asOf=self.as_of.isoformat(),
                    window=dict(start=self.start.isoformat(), end=self.end.isoformat()),
                    sourcePublicationCountNotOpenings=True, uniqueVacancyOpenings=None,
                    metrics=dict(sorted(totals.items())), bySegment={k: dict(v) for k, v in sorted(segments.items())},
                    byCompany=[dict(companyId=cid, name=self.scope[cid].get("name"), metrics=dict(counts[cid])) for cid in self.scope],
                    coverageStatuses=dict(status_counts), missingCompanies=missing, fullTextDuplicates=duplicates,
                    identities={k: sorted(v) for k, v in identities.items()}, findings=self.findings, files=self.files,
                    summary=dict(errors=sum(i["severity"] == "error" for i in self.findings),
                                 warnings=sum(i["severity"] == "warning" for i in self.findings), companiesRead=len(self.seen)))


def compare(report, before):
    after_m, before_m = report["metrics"], before["metrics"]
    out = dict(metrics={k: dict(before=before_m.get(k, 0), after=after_m.get(k, 0), delta=after_m.get(k, 0) - before_m.get(k, 0))
                        for k in sorted(set(after_m) | set(before_m))}, identityChanges={})
    for key, values in report["identities"].items():
        a, b = set(values), set(before.get("identities", {}).get(key, []))
        out["identityChanges"][key] = dict(added=len(a-b), removed=len(b-a), addedIdentities=sorted(a-b), removedIdentities=sorted(b-a))
    def signature(f):
        return json.dumps({k: f.get(k) for k in ["code", "companyId", "id", "reference", "field", "url", "address"]}, sort_keys=True)
    old_findings = {signature(f) for f in before.get("findings", [])}
    out["inheritedFindings"] = [f for f in report["findings"] if signature(f) in old_findings]
    out["newFindings"] = [f for f in report["findings"] if signature(f) not in old_findings]
    out["newErrors"] = sum(f["severity"] == "error" for f in out["newFindings"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scope", type=Path, required=True, help="JSON list of companies or a manifest with companies")
    ap.add_argument("--input", type=Path, action="append", required=True, help="Typed JSON file or directory; never recursively scanned")
    ap.add_argument("--phase", choices=["baseline", "staged", "projection"], default="staged")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--before", type=Path)
    ap.add_argument("--receipt-dir", type=Path, action="append", default=[], help="Allow targeted reads of contact receipt IDs in these dirs, never recursive scanning")
    ap.add_argument("--contact-proof-input", type=Path, action="append", default=[], help="Native typed contact sidecar dir/file for projection proof only; not counted as data")
    ap.add_argument("--as-of", default="2026-10-14T23:59:59+03:00")
    ap.add_argument("--window-start", default="2026-04-14T00:00:00+03:00")
    ap.add_argument("--window-end", default="2026-10-14T23:59:59+03:00")
    ap.add_argument("--require-complete", action="store_true")
    args = ap.parse_args()
    dates = [parse_date(x) for x in [args.window_start, args.window_end, args.as_of]]
    if not all(dates) or dates[0] > dates[1]: ap.error("Invalid window/as-of dates")
    scope = json.loads(args.scope.read_text())
    if isinstance(scope, dict): scope = scope["companies"]
    scope = {x.get("id", x.get("companyId")): x for x in scope}
    if None in scope or not scope: ap.error("Scope must contain company IDs")
    output = args.output_dir.resolve()
    for src in args.input:
        src = src.resolve()
        if src == output or (src.is_dir() and src in output.parents): ap.error("Report directory must be outside input directories")
    audit = Audit(scope, args.phase, *dates, receipt_dirs=args.receipt_dir)
    proof_paths = sorted({p.resolve() for src in args.contact_proof_input for p in (src.glob("*.json") if src.is_dir() else [src])})
    for path in proof_paths: audit.read_proof_file(path)
    paths = sorted({p.resolve() for src in args.input for p in (src.glob("*.json") if src.is_dir() else [src])})
    for path in paths: audit.read_file(path)
    report = audit.finish(args.require_complete)
    if args.before: report["comparison"] = compare(report, json.loads(args.before.read_text()))
    output.mkdir(parents=True, exist_ok=True)
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Аудит данных сегментов", "", f"Фаза: {args.phase}. Прочитано компаний: {report['summary']['companiesRead']}/{len(scope)}.",
             f"Ошибок: {report['summary']['errors']}; предупреждений: {report['summary']['warnings']}.", "",
             "Публикации о найме считаются отдельно от уникальных позиций; число позиций неизвестно.", "", "| Метрика | Количество |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in report["metrics"].items()]
    lines += ["", "## Диагностика", ""]
    for code, n in Counter(x["code"] for x in report["findings"]).most_common(): lines.append(f"- `{code}`: {n}")
    lines += ["", "Точные ID, доказательства, покрытие и сравнение идентичностей находятся в audit.json. Скрипт не менял входные файлы и не обращался к сети."]
    (output / "audit.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(dict(summary=report["summary"], metrics=report["metrics"], output=str(output)), ensure_ascii=False))
    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
