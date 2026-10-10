import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

try:
    import tvbox_source_api as api
except ModuleNotFoundError:
    api = None


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(api, 'collector module must implement the source API contract')

    def test_discovers_only_tvbox_category_url_not_display_link(self):
        resources = {'resources': [
            {'name': 'Box', 'category': 'tvbox_config', 'url': 'https://box.example/config', 'link_url': 'https://home.example/'},
            {'name': 'Film', 'category': 'online_video', 'url': 'https://other.example/'},
            {'name': 'Dup', 'category': 'tvbox_config', 'url': 'https://box.example/config'},
        ]}
        self.assertEqual(api.discover(resources), [('Box', 'https://box.example/config')])

    def test_jsonc_preserves_urls_and_comment_markers_inside_strings(self):
        raw = b'\xef\xbb\xbf{/* hello */"sites":[{"api":"https://a.example/?x=//", // hi\n"name":"/*literal*/",},],}'
        parsed = api.parse_document(raw)
        self.assertEqual(parsed['sites'][0]['api'], 'https://a.example/?x=//')
        self.assertEqual(parsed['sites'][0]['name'], '/*literal*/')
        for bad in [b'<html>Oops</html>', b'\x89PNG\r\n', b'2423encrypted', b'{"sites": [}']:
            with self.assertRaises(api.SourceError):
                api.parse_document(bad)

    def test_type_compatibility_urls_headers_and_stable_unique_keys(self):
        self.assertTrue(api.normalize_site({'name': '   ', 'type': 1, 'api': '/api'}, 'https://box.example/config')['name'].strip())
        raw = {'key': 'same', 'name': 'Example', 'type': 1, 'api': '../api.php', 'playUrl': '/play?url=',
               'ext': {'id': 1}, 'header': {'User-Agent': 'Test'}}
        a = api.normalize_site(raw, 'https://box.example/c/config.json')
        self.assertEqual(a['api'], 'https://box.example/api.php')
        self.assertEqual(a['playUrl'], 'https://box.example/play?url=')
        self.assertEqual(a['ext'], {'id': 1})
        self.assertEqual(a['header'], {'User-Agent': 'Test'})
        self.assertEqual(a['key'], api.normalize_site(dict(raw, name='Renamed'), 'https://box.example/c/config.json')['key'])
        self.assertEqual(a['key'], api.normalize_site(dict(raw, api='https://other.example/api'), 'https://box.example/c/config.json')['key'])
        self.assertNotEqual(a['key'], api.normalize_site(raw, 'https://different.example/config')['key'])
        self.assertEqual(a['key'], api.normalize_site(raw, 'https://cdn.example/config', source_url='https://box.example/c/config.json')['key'])
        for unsupported in [dict(raw, type=3), dict(raw, searchable=0), dict(raw, searchable=False), dict(raw, searchable='0'), dict(raw, api='javascript:alert(1)')]:
            with self.assertRaises(api.SourceError):
                api.normalize_site(unsupported, 'https://box.example/config')

    def test_search_request_matches_kazumi_and_preserves_existing_query(self):
        site = {'type': 0, 'api': 'https://api.example/?token=public&wd=old', 'ext': {'hello': 'world'}, 'header': {'X-Test': 'yes'}}
        request = api.search_request(site, '测试')
        query = parse_qs(urlsplit(request.url).query)
        self.assertEqual(query, {'token': ['public'], 'wd': ['测试'], 'quick': ['false'], 'ac': ['list'], 'extend': ['{"hello":"world"}']})
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.headers['X-Test'], 'yes')
        request = api.search_request(dict(site, type=4, ext='a' * 1001), '测试')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(parse_qs(urlsplit(request.url).query), {})
        self.assertEqual(parse_qs(request.body.decode())['token'], ['public'])
        self.assertEqual(parse_qs(request.body.decode())['extend'], ['a' * 1001])
        self.assertNotIn('ac', parse_qs(request.body.decode()))

    def test_probe_requires_protocol_and_nonempty_usable_search_result(self):
        for site_type in [1, 2, 4]:
            self.assertEqual(api.validate_search(b'{"list":[{"vod_id":"1","vod_name":"Movie"}]}', site_type), 1)
            for body in [b'{}', b'{"list":[]}', b'{"list":[{"message":"hi"}]}', b'<html>OK</html>']:
                with self.assertRaises(api.SourceError):
                    api.validate_search(body, site_type)
        self.assertEqual(api.validate_search(b'<rss><list><video><id>1</id><name>Movie</name></video></list></rss>', 0), 1)
        for body in [b'<html>OK</html>', b'<rss><list/></rss>', b'<!DOCTYPE x [<!ENTITY e "value">]><rss/>']:
            with self.assertRaises(api.SourceError):
                api.validate_search(body, 0)

    def test_public_target_checks_and_redacted_reports(self):
        for url in ['file:///tmp/config', 'http://127.0.0.1/', 'http://[::1]/', 'http://user:secret@public.example/', 'http://169.254.169.254/', 'http://10.0.0.1/', 'http://224.0.0.1/']:
            with self.assertRaises(api.SourceError):
                api.public_target(url, resolver=lambda *args, **kwargs: [(2, 1, 6, '', ('127.0.0.1', 80))])
        resolver = lambda *a, **kw: [(2, 1, 6, '', ('93.184.216.34', 443))]
        self.assertEqual(api.public_target('https://public.example/a', resolver=resolver)[2], ['93.184.216.34'])
        self.assertNotIn('secret', api.redact_url('https://user:secret@example.org/a?token=secret'))
        self.assertNotIn('user', api.redact_url('https://user:secret@example.org/a?token=secret'))

    def test_recursive_warehouses_dedupe_protocol_probe_and_unsupported_report(self):
        bodies = {
            'https://box.example/root': {'urls': [{'name': 'A', 'url': './a'}, {'name': 'Cycle', 'url': './root'}, {'name': 'Bad', 'url': './bad'}]},
            'https://box.example/a': {'storeHouse': [{'sourceName': 'B', 'sourceUrl': './b'}]},
            'https://box.example/b': {'sites': [
                {'key': 'same', 'name': 'JSON', 'type': 1, 'api': '/api?token=secret'},
                {'key': 'same', 'name': 'Duplicate', 'type': 1, 'api': '/api?token=secret'},
                {'key': 'spider', 'type': 3, 'api': 'csp_Test'}]},
            'https://box.example/bad': '<html>landing</html>',
        }
        calls = []
        def fetch(request):
            calls.append(request.url)
            if '/api?' in request.url:
                return api.Response(request.url, b'{"list":[{"vod_id":1,"vod_name":"Movie"}]}')
            data = bodies[request.url]
            return api.Response(request.url, (json.dumps(data) if isinstance(data, dict) else data).encode())
        collector = api.Collector(fetch=fetch, limits=api.Limits(workers=2))
        sites, warehouses, report = collector.collect([('Root', 'https://box.example/root')])
        self.assertEqual(len(sites), 1)
        self.assertEqual(warehouses, [{'name': 'B', 'url': 'https://box.example/b'}])
        self.assertEqual(len([url for url in calls if '/api?' in url]), 1)
        self.assertEqual(report['counts']['compatible_sites'], 1)
        self.assertEqual(report['counts']['valid_configs'], 1)
        self.assertIn('unsupported_type', str(report))
        self.assertNotIn('secret', json.dumps(report))

    def test_limits_bound_config_traversal_and_sites(self):
        def fetch(request):
            if request.url.endswith('/root'):
                value = {'urls': [{'name': str(i), 'url': './' + str(i)} for i in range(20)]}
            elif 'wd=' in request.url:
                value = {'list': [{'vod_id': '1', 'vod_name': 'Movie'}]}
            else:
                value = {'sites': [{'name': str(i), 'type': 1, 'api': '/api/' + str(i)} for i in range(20)]}
            return api.Response(request.url, json.dumps(value).encode())
        collector = api.Collector(fetch=fetch, limits=api.Limits(max_configs=2, max_sites=3, workers=2))
        sites, _, report = collector.collect([('Root', 'https://box.example/root')])
        self.assertEqual(len(sites), 3)
        self.assertEqual(report['counts']['configs_fetched'], 2)
        self.assertTrue(report['truncated'])
        collector = api.Collector(fetch=fetch, limits=api.Limits(max_depth=0, workers=1))
        sites, _, report = collector.collect([('Root', 'https://box.example/root')])
        self.assertEqual(report['counts']['configs_fetched'], 1)
        self.assertFalse(sites)
        self.assertTrue(report['truncated'])

    def test_failed_build_preserves_last_good_configuration_and_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / 'kazumi.json').write_text('{"sites":["previous"]}')
            self.assertFalse(api.publish(out, [], [], {'status': 'failed'}))
            self.assertEqual((out / 'kazumi.json').read_text(), '{"sites":["previous"]}')
            self.assertEqual(json.loads((out / 'report.json').read_text())['status'], 'failed')
            self.assertTrue(api.publish(out, [{'key': 'new'}], [{'name': 'B', 'url': 'https://example.org/b'}], {'status': 'ok'}))
            self.assertEqual(json.loads((out / 'kazumi.json').read_text())['sites'], [{'key': 'new'}])
            self.assertIn('laoma528', (out / 'ATTRIBUTION.md').read_text())


class TransportTests(unittest.TestCase):
    def test_leading_jsonc_comments_before_root_are_supported(self):
        self.assertEqual(api.parse_document(b'// published config\n/* version 1 */ {"sites": []}'), {'sites': []})

    def test_headers_are_case_insensitive_and_site_user_agent_overrides_default(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: default
        response.read1.return_value = b''
        with patch.object(api, 'public_target', return_value=('public.example', 80, ['93.184.216.34'])), patch.object(api.socket, 'create_connection'), patch.object(api.http.client, 'HTTPConnection', return_value=connection):
            api.HTTPFetcher(api.Limits())(api.Request('http://public.example/', headers={'user-agent': 'CustomUA'}))
        headers = connection.request.call_args.kwargs['headers']
        self.assertEqual([value for key, value in headers.items() if key.lower() == 'user-agent'], ['CustomUA'])

    def test_private_redirect_is_never_connected(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 302
        response.getheader.return_value = 'http://127.0.0.1/private'
        def target(url):
            if '127.0.0.1' in url:
                raise api.SourceError('non_public_target')
            return 'public.example', 80, ['93.184.216.34']
        with patch.object(api, 'public_target', side_effect=target), patch.object(api.socket, 'create_connection') as connect, patch.object(api.http.client, 'HTTPConnection', return_value=connection):
            with self.assertRaisesRegex(api.SourceError, 'non_public_target'):
                api.HTTPFetcher(api.Limits())(api.Request('http://public.example/'))
            self.assertEqual(connect.call_count, 1)

    def test_streaming_body_limit_and_http_error_classification(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: default
        response.read1.return_value = b'12345'
        with patch.object(api, 'public_target', return_value=('public.example', 80, ['93.184.216.34'])), patch.object(api.socket, 'create_connection'), patch.object(api.http.client, 'HTTPConnection', return_value=connection):
            with self.assertRaisesRegex(api.SourceError, 'body_too_large'):
                api.HTTPFetcher(api.Limits(max_bytes=4))(api.Request('http://public.example/'))
            response.status = 403
            with self.assertRaisesRegex(api.SourceError, 'http_403'):
                api.HTTPFetcher(api.Limits())(api.Request('http://public.example/'))

    def test_private_play_url_is_rejected(self):
        with self.assertRaisesRegex(api.SourceError, 'non_public_target'):
            api.normalize_site({'name': 'A', 'type': 1, 'api': '/api', 'playUrl': 'http://127.0.0.1/'}, 'https://public.example/config')

    def test_cli_upstream_failure_returns_nonzero_and_redacts_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'kazumi.json').write_text('{"sites":["old"]}')
            with patch.object(api.HTTPFetcher, '__call__', side_effect=api.SourceError('http_403')):
                self.assertEqual(api.main(['--output-dir', directory]), 1)
            self.assertEqual((output / 'kazumi.json').read_text(), '{"sites":["old"]}')
            self.assertEqual(json.loads((output / 'report.json').read_text())['reason'], 'http_403')

class ReviewRegressionTests(unittest.TestCase):
    def test_duplicate_representative_independent_of_seed_order(self):
        def fetch(request):
            data = {'list': [{'vod_id': 1, 'vod_name': 'Movie'}]} if 'wd=' in request.url else {
                'sites': [{'key': 'same', 'name': 'Source', 'type': 1, 'api': 'https://api.example/api'}]}
            return api.Response(request.url, json.dumps(data).encode())
        seeds = [('Z', 'https://z.example/config'), ('A', 'https://a.example/config')]
        first = api.Collector(fetch=fetch).collect(seeds)[0]
        second = api.Collector(fetch=fetch).collect(list(reversed(seeds)))[0]
        self.assertEqual(first, second)
        expected = api.normalize_site({'key': 'same', 'name': 'Source', 'type': 1, 'api': 'https://api.example/api'}, 'https://a.example/config')
        self.assertEqual(first, [expected])

    def test_duplicate_representative_independent_of_warehouse_order(self):
        children = [{'name': 'Z', 'url': 'https://z.example/config'}, {'name': 'A', 'url': 'https://a.example/config'}]
        def collect(rows):
            def fetch(request):
                if request.url.endswith('/root'):
                    data = {'urls': rows}
                elif 'wd=' in request.url:
                    data = {'list': [{'vod_id': 1, 'vod_name': 'Movie'}]}
                else:
                    data = {'sites': [{'key': 'same', 'name': 'Source', 'type': 1, 'api': 'https://api.example/api'}]}
                return api.Response(request.url, json.dumps(data).encode())
            return api.Collector(fetch=fetch).collect([('Root', 'https://box.example/root')])[0]
        self.assertEqual(collect(children), collect(list(reversed(children))))

    def test_network_tries_next_public_ip_after_connection_failure(self):
        from unittest.mock import MagicMock
        connection, connected_socket = MagicMock(), MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: default
        response.read1.return_value = b''
        with patch.object(api, 'public_target', return_value=('public.example', 80, ['93.184.216.1', '93.184.216.2'])), patch.object(api.socket, 'create_connection', side_effect=[OSError('no route'), connected_socket]) as connect, patch.object(api.http.client, 'HTTPConnection', return_value=connection):
            result = api.HTTPFetcher(api.Limits())(api.Request('http://public.example/'))
            self.assertEqual(result.status, 200)
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(connect.call_args_list[1].args[0], ('93.184.216.2', 80))

    def test_html_video_tags_are_not_xml_protocol_and_malformed_sites_not_warehouse(self):
        with self.assertRaises(api.SourceError):
            api.validate_search(b'<html><video><id>1</id><name>Movie</name></video></html>', 0)
        collector = api.Collector(fetch=lambda request: api.Response(request.url, b'{"sites":[{"garbage":1}]}'))
        sites, warehouses, report = collector.collect([('Bad', 'https://box.example/config')])
        self.assertFalse(sites)
        self.assertFalse(warehouses)
        self.assertEqual(report['counts']['valid_configs'], 0)


if __name__ == '__main__':
    unittest.main()
