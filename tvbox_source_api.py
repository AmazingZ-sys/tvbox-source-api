#!/usr/bin/env python3
"""Collect public TVBox configs and publish a probed HTTP-only Kazumi subset."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

UPSTREAM = 'https://raw.githubusercontent.com/laoma528/awesome-zhuiju-free/main/resources/resources.json'
USER_AGENT = 'tvbox-source-api/1.0 (+https://github.com/AmazingZ-sys/tvbox-source-api)'


class SourceError(Exception):
    """A stable error code, safe to include in public diagnostics."""


@dataclass(frozen=True)
class Limits:
    timeout: float = 12
    max_bytes: int = 4 * 1024 * 1024
    workers: int = 8
    max_depth: int = 4
    max_configs: int = 100
    max_sites: int = 300
    max_redirects: int = 4


@dataclass(frozen=True)
class Request:
    url: str
    method: str = 'GET'
    headers: dict = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class Response:
    url: str
    body: bytes
    status: int = 200
    content_type: str = ''


def canonical_url(value: str, base: str = '') -> str:
    """Resolve and validate URL syntax without making a network request."""
    if not isinstance(value, str) or not value.strip():
        raise SourceError('invalid_url')
    try:
        parsed = urlsplit(urljoin(base, value.strip()))
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            raise SourceError('http_only')
        if parsed.username is not None or parsed.password is not None:
            raise SourceError('credentials_in_url')
        if any(ord(c) < 32 for c in value) or '\\' in value or '%' in parsed.hostname:
            raise SourceError('invalid_url')
        hostname = parsed.hostname.encode('idna').decode('ascii').lower()
        port = parsed.port
        try:
            literal_ip = ipaddress.ip_address(hostname)
        except ValueError:
            literal_ip = None
        if literal_ip and (not literal_ip.is_global or literal_ip.is_multicast or literal_ip.is_reserved):
            raise SourceError('non_public_target')
        host = '[' + hostname + ']' if ':' in hostname else hostname
        default_port = 443 if parsed.scheme.lower() == 'https' else 80
        if port is not None and port != default_port:
            host += ':' + str(port)
        return urlunsplit((parsed.scheme.lower(), host,
                           quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~"),
                           quote(parsed.query, safe="%=&?/:@!$'()*+,;~-._"), ''))
    except (ValueError, UnicodeError) as exc:
        raise SourceError('invalid_url') from exc


def redact_url(value: str) -> str:
    """Never include URL userinfo or query values in a published report."""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ''
        if ':' in host:
            host = '[' + host + ']'
        if parts.port:
            host += ':' + str(parts.port)
        return urlunsplit((parts.scheme, host, parts.path,
                           urlencode([(k, '[redacted]') for k, _ in parse_qsl(parts.query)]), ''))
    except (ValueError, TypeError):
        return '[invalid URL]'


def public_target(url: str, resolver=None):
    """Resolve once and validate every address; callers connect to these exact IPs."""
    url = canonical_url(url)
    parts = urlsplit(url)
    hostname = parts.hostname
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    try:
        addresses = (resolver or socket.getaddrinfo)(hostname, port, type=socket.SOCK_STREAM)
        ips = sorted({address[4][0] for address in addresses})
        if not ips:
            raise SourceError('dns_failed')
        for ip in ips:
            addr = ipaddress.ip_address(ip)
            if not addr.is_global or addr.is_multicast or addr.is_reserved:
                raise SourceError('non_public_target')
        return hostname, port, ips
    except (OSError, ValueError) as exc:
        raise SourceError('dns_failed') from exc


class HTTPFetcher:
    """Direct HTTP(S), public pinned DNS, bounded bodies and redirect validation.

    Proxy environment variables are deliberately ignored. A fresh connection is
    made to a validated IP while TLS and Host retain the original hostname.
    """
    def __init__(self, limits: Limits):
        self.limits = limits
        self.context = ssl.create_default_context()

    def __call__(self, request: Request) -> Response:
        current = canonical_url(request.url)
        method, body = request.method, request.body
        headers = {'user-agent': USER_AGENT, 'accept': '*/*', 'accept-encoding': 'identity'}
        forbidden = {'host', 'content-length', 'connection', 'transfer-encoding', 'accept-encoding'}
        for key, value in request.headers.items():
            if key.lower() in forbidden:
                continue
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or '\r' in value or '\n' in value:
                raise SourceError('invalid_header')
            headers[key.lower()] = value
        deadline = time.monotonic() + self.limits.timeout
        for hop in range(self.limits.max_redirects + 1):
            conn = None
            sock = None
            try:
                hostname, port, ips = public_target(current)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceError('timeout')
                # Divide the remaining budget so one dead address cannot starve
                # every other validated address (including IPv4/IPv6 fallback).
                last_error = None
                for index, ip in enumerate(ips):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SourceError('timeout')
                    per_address = remaining / (len(ips) - index)
                    try:
                        sock = socket.create_connection((ip, port), timeout=per_address)
                        if urlsplit(current).scheme == 'https':
                            sock.settimeout(min(per_address, max(0.001, deadline - time.monotonic())))
                            sock = self.context.wrap_socket(sock, server_hostname=hostname)
                        break
                    except OSError as exc:
                        last_error = exc
                        if sock:
                            sock.close()
                        sock = None
                if sock is None:
                    raise last_error or OSError('no usable public address')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceError('timeout')
                sock.settimeout(remaining)
                conn = http.client.HTTPConnection(hostname, port, timeout=remaining)
                conn.sock = sock
                parts = urlsplit(current)
                target = urlunsplit(('', '', parts.path or '/', parts.query, ''))
                conn.request(method, target, body=body, headers=headers)
                response = conn.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    if hop == self.limits.max_redirects:
                        raise SourceError('redirect_limit')
                    location = response.getheader('Location')
                    if not location:
                        raise SourceError('redirect_without_location')
                    next_url = canonical_url(location, current)
                    if urlsplit(next_url).netloc != parts.netloc or urlsplit(next_url).scheme != parts.scheme:
                        # Config-provided secrets must not follow cross-origin redirects.
                        headers = {k: v for k, v in headers.items() if k.lower() not in ('authorization', 'cookie', 'proxy-authorization')}
                    if response.status == 303 or (response.status in (301, 302) and method == 'POST'):
                        method, body = 'GET', None
                    current = next_url
                    continue
                if not 200 <= response.status < 300:
                    raise SourceError('http_' + str(response.status))
                length = response.getheader('Content-Length')
                if length and int(length) > self.limits.max_bytes:
                    raise SourceError('body_too_large')
                if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                    raise SourceError('unsupported_content_encoding')
                chunks, size = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SourceError('timeout')
                    sock.settimeout(remaining)
                    chunk = response.read1(min(65536, self.limits.max_bytes + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > self.limits.max_bytes:
                        raise SourceError('body_too_large')
                return Response(current, b''.join(chunks), response.status, response.getheader('Content-Type', ''))
            except SourceError:
                raise
            except (socket.timeout, TimeoutError) as exc:
                raise SourceError('timeout') from exc
            except ssl.SSLError as exc:
                raise SourceError('tls_failed') from exc
            except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
                raise SourceError('network_failed') from exc
            finally:
                if conn:
                    conn.close()
                elif sock:
                    sock.close()
        raise SourceError('redirect_limit')


def parse_document(raw: bytes):
    """JSONC only: BOM, // and /* */ comments, and trailing commas.

    Strings are scanned separately so URLs and comment-like string content stay
    untouched. No eval, JavaScript, Python, JARs or encrypted payloads are run.
    """
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise SourceError('binary_or_non_utf8') from exc
    if text.lstrip().startswith('<'):
        raise SourceError('html_or_xml_not_config')
    tokens, i, size = [], 0, len(text)
    while i < size:
        if text[i] == '"':
            start = i
            i += 1
            while i < size:
                if text[i] == '\\':
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            tokens.append(text[start:i])
        elif text.startswith('//', i):
            end = text.find('\n', i + 2)
            i = size if end < 0 else end
            tokens.append(' ')
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise SourceError('invalid_jsonc')
            i = end + 2
            tokens.append(' ')
        else:
            tokens.append(text[i])
            i += 1
    for i, token in enumerate(tokens):
        if token == ',':
            following = i + 1
            while following < len(tokens) and tokens[following].isspace():
                following += 1
            if following < len(tokens) and tokens[following] in ('}', ']'):
                tokens[i] = ''
    cleaned = ''.join(tokens)
    if not cleaned.lstrip().startswith(('{', '[')):
        raise SourceError('encrypted_or_unsupported_content')
    try:
        return json.loads(cleaned, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, RecursionError) as exc:
        raise SourceError('invalid_jsonc') from exc


def discover(document) -> list[tuple[str, str]]:
    if not isinstance(document, dict) or not isinstance(document.get('resources'), list):
        raise SourceError('upstream_schema_changed')
    result, seen = [], set()
    for row in document['resources']:
        if not isinstance(row, dict) or row.get('category') != 'tvbox_config':
            continue
        url = row.get('url')
        if isinstance(url, str) and url not in seen:
            result.append((str(row.get('name') or row.get('id') or 'TVBox'), url))
            seen.add(url)
    if not result:
        raise SourceError('no_tvbox_resources')
    return result


def normalize_site(raw, base: str, source_url: str | None = None) -> dict:
    if not isinstance(raw, dict):
        raise SourceError('invalid_site')
    try:
        value = raw.get('type')
        if isinstance(value, bool) or str(value) not in ('0', '1', '2', '4'):
            raise SourceError('unsupported_type')
        site_type = int(value)
        if str(raw.get('searchable', 1)).lower() in ('0', 'false'):
            raise SourceError('search_disabled')
        url = canonical_url(raw.get('api', ''), base)
        # Reject literal local URLs even before the network probe.
        hostname = urlsplit(url).hostname
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and (not address.is_global or address.is_multicast or address.is_reserved):
            raise SourceError('non_public_target')
        name = str(raw.get('name') or '').strip() or str(raw.get('key') or '').strip() or hostname
        site = {'name': name, 'type': site_type, 'api': url}
        play_url = raw.get('playUrl')
        if play_url:
            site['playUrl'] = canonical_url(play_url, base)
        header_value = raw.get('headers', raw.get('header'))
        if header_value not in (None, ''):
            if isinstance(header_value, str):
                header_value = json.loads(header_value)
            if not isinstance(header_value, dict):
                raise SourceError('invalid_header')
            headers = {str(k): str(v) for k, v in header_value.items()}
            site['headers' if 'headers' in raw else 'header'] = headers
        if 'ext' in raw:
            site['ext'] = raw['ext']
        original_key = str(raw.get('key') or '').strip()
        identity = {'source': canonical_url(source_url or base),
                    'site': original_key or site_fingerprint(site)}
        encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        site['key'] = 'tvbox-' + hashlib.sha256(encoded).hexdigest()[:32]
        return site
    except (TypeError, ValueError, RecursionError) as exc:
        raise SourceError('invalid_site') from exc


def site_fingerprint(site: dict) -> str:
    identity = {'type': site['type'], 'api': site['api'], 'playUrl': site.get('playUrl', ''),
                'headers': {k.lower(): v for k, v in site.get('headers', site.get('header', {})).items()},
                'ext': site.get('ext')}
    return json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def search_request(site: dict, keyword: str) -> Request:
    parts = urlsplit(site['api'])
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({'wd': keyword, 'quick': 'false'})
    if site['type'] == 0:
        query['ac'] = 'list'
    ext = site.get('ext')
    ext = ext if isinstance(ext, str) else '' if ext is None else json.dumps(ext, ensure_ascii=False, separators=(',', ':'))
    if ext:
        query['extend'] = ext
    # Dart String.length counts UTF-16 code units, including surrogate pairs.
    post = len(ext.encode('utf-16-le')) // 2 > 1000
    encoded = urlencode(query)
    headers = dict(site.get('headers', site.get('header', {})))
    if post:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, '' if post else encoded, ''))
    return Request(url, 'POST' if post else 'GET', headers, encoded.encode() if post else None)


def validate_search(raw: bytes, site_type: int) -> int:
    try:
        text = raw.decode('utf-8-sig')
        if site_type == 0:
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                raise SourceError('unsafe_xml')
            root = ET.fromstring(text)
            if root.tag != 'rss' or root.find('list') is None:
                raise SourceError('invalid_search_protocol')
            nodes = list(root.find('list').iter('video'))
            count = sum(bool((n.findtext('id') or '').strip() and (n.findtext('name') or '').strip()) for n in nodes)
        else:
            document = json.loads(text)
            if not isinstance(document, dict) or not isinstance(document.get('list'), list):
                raise SourceError('invalid_search_protocol')
            count = sum(isinstance(n, dict) and isinstance(n.get('vod_id'), (str, int))
                        and not isinstance(n.get('vod_id'), bool) and bool(str(n['vod_id']).strip())
                        and isinstance(n.get('vod_name'), str) and bool(n['vod_name'].strip()) for n in document['list'])
        if not count:
            raise SourceError('empty_search_results')
        return count
    except SourceError:
        raise
    except (ValueError, UnicodeError, ET.ParseError, RecursionError) as exc:
        raise SourceError('invalid_search_protocol') from exc


class Collector:
    def __init__(self, fetch=None, limits: Limits | None = None, keyword: str = '海贼王'):
        self.limits = limits or Limits()
        self.fetch = fetch or HTTPFetcher(self.limits)
        self.keyword = keyword

    def collect(self, seeds):
        report = {'schema_version': 1, 'tested_at': datetime.now(timezone.utc).isoformat(),
                  'probe_keyword': self.keyword, 'scope': 'config_and_search_only',
                  'truncated': False, 'configs': [], 'sites': [], 'limits': vars(self.limits)}
        configs, visited, candidates = [], set(), {}
        frontier = [(name, url, 0) for name, url in seeds]
        fetched = 0

        def load(row):
            name, url, depth = row
            record = {'name': name, 'url': redact_url(url), 'depth': depth}
            try:
                response = self.fetch(Request(url))
                document = parse_document(response.body)
                if not isinstance(document, dict):
                    raise SourceError('invalid_config_shape')
                record.update({'status': 'ok', 'http_status': response.status, 'final_url': redact_url(response.url)})
                return row, response.url, document, record
            except SourceError as exc:
                record.update({'status': 'failed', 'reason': str(exc)})
                return row, url, None, record

        with ThreadPoolExecutor(max_workers=self.limits.workers) as pool:
            while frontier:
                batch = []
                for name, value, depth in sorted(frontier, key=lambda row: (row[1], row[0])):
                    try:
                        url = canonical_url(value)
                    except SourceError as exc:
                        report['configs'].append({'name': name, 'url': redact_url(value), 'status': 'failed', 'reason': str(exc)})
                        continue
                    if url in visited:
                        continue
                    if depth > self.limits.max_depth or fetched + len(batch) >= self.limits.max_configs:
                        report['truncated'] = True
                        continue
                    visited.add(url)
                    batch.append((name, url, depth))
                fetched += len(batch)
                frontier = []
                for row, final_url, document, record in pool.map(load, batch):
                    name, url, depth = row
                    report['configs'].append(record)
                    if document is None:
                        continue
                    has_sites = isinstance(document.get('sites'), list) and any(
                        isinstance(site, dict) and site.get('api') and 'type' in site for site in document['sites'])
                    containers = [document[k] for k in ('urls', 'storeHouse') if isinstance(document.get(k), list)]
                    if not has_sites and not containers:
                        record.update({'status': 'failed', 'reason': 'missing_sites_or_warehouse'})
                        continue
                    record['kind'] = 'config' if has_sites else 'warehouse'
                    if has_sites:
                        configs.append({'name': name, 'url': url})
                        for index, raw in enumerate(document['sites']):
                            try:
                                site = normalize_site(raw, final_url, source_url=url)
                            except SourceError as exc:
                                report['sites'].append({'config': redact_url(url), 'index': index, 'status': 'skipped', 'reason': str(exc)})
                                continue
                            fingerprint = site_fingerprint(site)
                            existing = candidates.get(fingerprint)
                            if existing:
                                # Stable representative even if warehouse order changes.
                                if (url, site['key']) < (existing[1], existing[0]['key']):
                                    candidates[fingerprint] = (site, url)
                            elif len(candidates) < self.limits.max_sites:
                                candidates[fingerprint] = (site, url)
                            else:
                                report['truncated'] = True
                    for container in containers:
                        for child in container:
                            if not isinstance(child, dict):
                                continue
                            child_url = child.get('url') or child.get('sourceUrl')
                            child_name = str(child.get('name') or child.get('sourceName') or name)
                            if not isinstance(child_url, str):
                                continue
                            try:
                                child_url = canonical_url(child_url, final_url)
                            except SourceError as exc:
                                report['configs'].append({'name': child_name, 'url': redact_url(child_url), 'status': 'failed', 'reason': str(exc)})
                                continue
                            frontier.append((child_name, child_url, depth + 1))

            def probe(item):
                site, source = item
                record = {'key': site['key'], 'config': redact_url(source), 'api': redact_url(site['api']), 'type': site['type']}
                try:
                    response = self.fetch(search_request(site, self.keyword))
                    count = validate_search(response.body, site['type'])
                    record.update({'status': 'ok', 'result_count': count, 'http_status': response.status})
                    return site, record
                except SourceError as exc:
                    record.update({'status': 'failed', 'reason': str(exc)})
                    return None, record

            # A malformed config may reuse a raw key for different APIs. Keep a
            # deterministic single representative so published keys stay unique.
            unique_keys = {}
            for fingerprint, item in sorted(candidates.items()):
                unique_keys.setdefault(item[0]['key'], item)
            candidates = unique_keys
            sites = []
            for site, record in pool.map(probe, candidates.values()):
                report['sites'].append(record)
                if site is not None:
                    sites.append(site)
        sites.sort(key=lambda site: site['key'])
        # A config can be found via multiple redirected aliases. Keep original
        # accessible URLs as requested, and don't synthesize incompatible spiders.
        configs = sorted({c['url']: c for c in configs}.values(), key=lambda c: c['url'])
        report['counts'] = {'upstream_resources': len(seeds), 'configs_fetched': fetched, 'valid_configs': len(configs),
                            'config_failures': sum(r['status'] == 'failed' for r in report['configs']),
                            'candidate_sites': len(candidates), 'compatible_sites': len(sites),
                            'site_failures': sum(r['status'] == 'failed' for r in report['sites']),
                            'skipped_sites': sum(r['status'] == 'skipped' for r in report['sites'])}
        report['status'] = 'ok' if sites else 'failed'
        return sites, configs, report


def atomic_json(path: Path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def publish(output: Path, sites, warehouses, report) -> bool:
    atomic_json(output / 'report.json', report)
    if not sites:
        return False
    atomic_json(output / 'kazumi.json', {'sites': sites})
    atomic_json(output / 'warehouse.json', {'urls': warehouses})
    attribution = Path(__file__).with_name('ATTRIBUTION.md').read_text(encoding='utf-8')
    (output / 'ATTRIBUTION.md').write_text(attribution, encoding='utf-8')
    (output / '.nojekyll').touch()
    return True


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-url', default=UPSTREAM)
    parser.add_argument('--resources-file', type=Path, help='offline upstream JSON fixture; config/API requests still use the network')
    parser.add_argument('--output-dir', type=Path, default=Path('public'))
    parser.add_argument('--probe-keyword', default='海贼王')
    parser.add_argument('--timeout', type=positive, default=12)
    parser.add_argument('--workers', type=positive, default=8)
    parser.add_argument('--max-bytes', type=positive, default=4 * 1024 * 1024)
    parser.add_argument('--max-depth', type=int, default=4)
    parser.add_argument('--max-configs', type=positive, default=100)
    parser.add_argument('--max-sites', type=positive, default=300)
    args = parser.parse_args(argv)
    if args.max_depth < 0 or not args.probe_keyword.strip():
        parser.error('max-depth must be nonnegative and probe-keyword must not be blank')
    limits = Limits(timeout=args.timeout, workers=args.workers, max_bytes=args.max_bytes,
                    max_depth=args.max_depth, max_configs=args.max_configs, max_sites=args.max_sites)
    fetch = HTTPFetcher(limits)
    try:
        if args.resources_file:
            with args.resources_file.open('rb') as stream:
                raw = stream.read(limits.max_bytes + 1)
            if len(raw) > limits.max_bytes:
                raise SourceError('body_too_large')
        else:
            raw = fetch(Request(args.upstream_url)).body
        seeds = discover(parse_document(raw))
        sites, warehouses, report = Collector(fetch, limits, args.probe_keyword).collect(seeds)
    except (SourceError, OSError) as exc:
        sites, warehouses = [], []
        report = {'schema_version': 1, 'status': 'failed', 'tested_at': datetime.now(timezone.utc).isoformat(),
                  'reason': str(exc) if isinstance(exc, SourceError) else 'file_io_failed',
                  'counts': {'compatible_sites': 0}, 'configs': [], 'sites': []}
    report['upstream'] = {'repository': 'https://github.com/laoma528/awesome-zhuiju-free',
                          'resources_url': redact_url(args.upstream_url), 'category': 'tvbox_config',
                          'license': 'CC BY 4.0', 'input': 'local_file' if args.resources_file else 'live_http'}
    success = publish(args.output_dir, sites, warehouses, report)
    print(json.dumps({'status': report['status'], 'counts': report['counts'],
                      'report': str(args.output_dir / 'report.json')}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
