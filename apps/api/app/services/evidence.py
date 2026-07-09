from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import fitz
import httpx
import trafilatura

from ..config import get_settings


@dataclass
class FetchedDocument:
    url: str
    content: str
    content_type: str
    status_code: int
    title: str | None = None
    canonical_url: str | None = None
    links: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


class UnsafeUrlError(ValueError):
    pass


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []
        self.canonical_url: str | None = None
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs}
        if tag.casefold() == "a" and values.get("href"):
            self.links.append(urljoin(self.base_url, values["href"] or ""))
        if tag.casefold() == "link" and "canonical" in (values.get("rel") or "").casefold() and values.get("href"):
            self.canonical_url = urljoin(self.base_url, values["href"] or "")
        if tag.casefold() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data.strip())


async def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("only public HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL credentials are not allowed")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise UnsafeUrlError("local addresses are not allowed")

    def resolve() -> list[str]:
        return list({item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)})

    try:
        addresses = await asyncio.to_thread(resolve)
    except socket.gaierror as exc:
        raise UnsafeUrlError("hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeUrlError("private, loopback, or reserved addresses are not allowed")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def verify_quote(content: str, quote: str) -> bool:
    normalized_quote = normalize_text(quote)
    return len(normalized_quote) >= 8 and normalized_quote in normalize_text(content)


def extract_content(raw: bytes, content_type: str, url: str) -> str:
    if "pdf" in content_type.lower() or urlparse(url).path.lower().endswith(".pdf"):
        document = fitz.open(stream=raw, filetype="pdf")
        return "\n\n".join(page.get_text("text") for page in document)
    decoded = raw.decode("utf-8", errors="replace")
    if "html" in content_type.lower() or "<html" in decoded[:1000].lower():
        return trafilatura.extract(decoded, include_comments=False, include_tables=True) or ""
    return decoded


async def fetch_document(url: str) -> FetchedDocument:
    settings = get_settings()
    current = url
    headers = {"User-Agent": "AtlasResearch/0.1 (+local cited research tool)"}
    async with httpx.AsyncClient(timeout=25, follow_redirects=False, headers=headers) as client:
        for _ in range(5):
            await validate_public_url(current)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect did not include a location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > settings.max_source_bytes:
                        raise ValueError("source exceeded maximum download size")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                content_type = response.headers.get("content-type", "text/plain").split(";", 1)[0]
                is_pdf = "pdf" in content_type.casefold() or urlparse(current).path.casefold().endswith(".pdf")
                if not (content_type.casefold().startswith("text/") or "html" in content_type.casefold() or is_pdf):
                    raise ValueError(f"unsupported source content type: {content_type}")
                content = extract_content(raw, content_type, current)
                if not content.strip():
                    raise ValueError("source did not contain extractable text")
                parser = _LinkParser(current)
                if "html" in content_type.casefold():
                    parser.feed(raw.decode("utf-8", errors="replace"))
                links = []
                for link in parser.links:
                    parsed_link = urlparse(link)
                    if parsed_link.scheme in {"http", "https"} and parsed_link.hostname:
                        links.append(link.split("#", 1)[0])
                title = " ".join(part for part in parser.title_parts if part).strip() or None
                return FetchedDocument(
                    current,
                    content,
                    content_type,
                    response.status_code,
                    title=title,
                    canonical_url=parser.canonical_url,
                    links=list(dict.fromkeys(links))[:500],
                )
    raise ValueError("too many redirects")
