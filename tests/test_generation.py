import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import generate
from digest_schema import validate_editorial, render_editorial

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/digest-v2.json').read_text())


def response(content, reason='stop'):
    return SimpleNamespace(id='mock-response', model='mock-model', usage=SimpleNamespace(model_dump=lambda: {'total_tokens': 42}),
                           choices=[SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content))])


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.record = copy.deepcopy(FIXTURE)
        self.articles = self.record['materials']
        self.editorial = self.record['editorial']
        self.minimum = patch.object(generate, 'MIN_ITEMS_PER_CATEGORY', 3)
        self.minimum.start()
        self.addCleanup(self.minimum.stop)

    def mocked_client(self, values):
        create = Mock(side_effect=values)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return create, patch.object(generate, 'OpenAI', return_value=client)

    def test_invalid_first_response_retries_with_feedback(self):
        create, mock = self.mocked_client([response('{}'), response(json.dumps(self.editorial))])
        run = {}
        with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            result = generate.generate_digest(self.articles, run)
        self.assertEqual(result, self.editorial)
        self.assertEqual(create.call_count, 2)
        self.assertIn('上次输出未通过校验', create.call_args_list[1].kwargs['messages'][1]['content'])
        self.assertEqual([a['status'] for a in run['attempts']], ['invalid', 'valid'])
        self.assertEqual(run['attempts'][1]['usage']['total_tokens'], 42)

    def test_empty_malformed_and_truncated_responses_fail_boundedly(self):
        for value in [response(''), response('not json'), response(json.dumps(self.editorial), 'length')]:
            with self.subTest(value=value):
                create, mock = self.mocked_client([value, value])
                with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
                    with self.assertRaisesRegex(RuntimeError, '2 次输出未通过校验'):
                        generate.generate_digest(self.articles)
                self.assertEqual(create.call_count, 2)

    def test_api_error_is_recorded_without_editorial_retry(self):
        create, mock = self.mocked_client([ConnectionError('provider unavailable')])
        run = {}
        with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            with self.assertRaises(ConnectionError):
                generate.generate_digest(self.articles, run)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(run['attempts'][0]['status'], 'api_error')

    def test_missing_forged_cross_category_reused_and_duplicate_sources_rejected(self):
        for refs in [[], ['invented'], [self.articles[3]['id']], [self.articles[1]['id']], [self.articles[0]['id']] * 2]:
            with self.subTest(refs=refs):
                value = copy.deepcopy(self.editorial)
                value['sections'][0]['items'][0]['source_ids'] = refs
                with self.assertRaises(ValueError):
                    validate_editorial(value, self.articles, 3)

    def test_model_cannot_supply_own_source_name_or_url(self):
        self.editorial['sections'][0]['items'][0]['url'] = 'https://invented.example/'
        with self.assertRaises(ValueError):
            validate_editorial(self.editorial, self.articles, 3)

    def test_duplicate_events_wrong_order_and_short_sections_rejected(self):
        mutations = [lambda v: v['sections'].reverse(),
                     lambda v: v['sections'][0]['items'].pop(),
                     lambda v: v['sections'][0]['items'][1].update(title=v['sections'][0]['items'][0]['title'])]
        for mutate in mutations:
            value = copy.deepcopy(self.editorial)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_editorial(value, self.articles, 3)

    def test_rendering_escapes_content_and_uses_material_sources(self):
        self.assertEqual(render_editorial(self.editorial, self.articles), self.record['content'])
        self.editorial['overview'] = '<script>alert(1)</script>'
        result = render_editorial(self.editorial, self.articles)
        self.assertNotIn('<script>', result)
        self.assertIn('研究机构 &lt;示例&gt;', result)
        self.assertIn('?a=1&amp;b=2', result)
        self.articles[0]['link'] = 'javascript:alert(1)'
        with self.assertRaises(ValueError):
            validate_editorial(self.editorial, self.articles, 3)

    def test_window_is_stable_when_execution_delayed(self):
        early = generate.issue_window(now=datetime(2026, 10, 12, 0, 17, tzinfo=timezone.utc))
        delayed = generate.issue_window(now=datetime(2026, 10, 12, 3, 17, tzinfo=timezone.utc))
        self.assertEqual(early, delayed)
        self.assertEqual(early[0], '2026-10-12')
        self.assertEqual(early[2] - early[1], timedelta(hours=generate.LOOKBACK_HOURS))
        previous = generate.issue_window(now=datetime(2026, 10, 12, 0, 16, tzinfo=timezone.utc))
        self.assertEqual(previous[0], '2026-10-11')
        self.assertEqual(generate.issue_window('2026-10-12', now=datetime(2026, 10, 14, tzinfo=timezone.utc)), early)
        with self.assertRaises(ValueError):
            generate.issue_window('2026-10-13', now=datetime(2026, 10, 12, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            generate.issue_window('2026-02-30')

    def test_collection_uses_half_open_window_and_stable_ids(self):
        start = datetime(2026, 10, 11, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        entries = [{'title': str(i), 'link': f'https://example.org/{i}', 'published_parsed': date.utctimetuple()}
                   for i, date in enumerate([start - timedelta(seconds=1), start, end - timedelta(seconds=1), end])]
        config = {'category': generate.CATEGORIES[0], 'name': 'source', 'url': 'https://example.org/rss'}
        with patch.object(generate, 'load_feeds', return_value=[config]), patch.object(generate, 'fetch_feed', return_value=SimpleNamespace(entries=entries, error=None)):
            first, _ = generate.collect_articles(start, end)
            second, _ = generate.collect_articles(start, end)
        self.assertEqual({a['title'] for a in first}, {'1', '2'})
        self.assertEqual([a['id'] for a in first], [a['id'] for a in second])

    def test_history_over_sixty_and_backfill_keeps_latest_first(self):
        start = datetime(2026, 1, 1)
        history = [{'date': (start + timedelta(days=i)).strftime('%Y-%m-%d')} for i in range(75)]
        result = generate.upsert_history(history, {'date': '2025-12-31'})
        self.assertEqual(len(result), 76)
        self.assertEqual(result[0]['date'], '2026-03-16')
        result = generate.upsert_history(result, {'date': '2026-01-01', 'updated': True})
        self.assertEqual(len(result), 76)
        self.assertEqual(sum(d['date'] == '2026-01-01' for d in result), 1)

    def test_atomic_write_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'history.json'
            path.write_text('original')
            with patch.object(generate.os, 'replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    generate.atomic_json(path, [])
            self.assertEqual(path.read_text(), 'original')
            self.assertEqual(len(list(Path(root).iterdir())), 1)

    def run_main(self, root, articles, api_values):
        path = Path(root)
        create, mock = self.mocked_client(api_values)
        with patch.multiple(generate, ROOT=path, DATA_DIR=path/'data', HISTORY_FILE=path/'data/digests.json', RUN_DIR=path/'.runs'), \
             patch.object(generate, 'collect_articles', return_value=(articles, [])), \
             patch.object(generate, 'issue_window', return_value=('2026-10-12', datetime(2026,10,11,0,17,tzinfo=timezone.utc), datetime(2026,10,12,0,17,tzinfo=timezone.utc))), \
             mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            generate.main([])
        return create

    def test_main_persists_evidence_and_structured_record(self):
        with tempfile.TemporaryDirectory() as root:
            self.run_main(root, self.articles, [response(json.dumps(self.editorial))])
            record = json.loads((Path(root)/'data/digests.json').read_text())[0]
            evidence = json.loads((Path(root)/record['generation']['evidence_path']).read_text())
            self.assertEqual(record['schema_version'], 2)
            self.assertEqual(record['materials'], self.articles)
            self.assertEqual(evidence['prompt_version'], generate.PROMPT_VERSION)
            self.assertEqual(evidence['attempts'][0]['raw_response'], json.dumps(self.editorial))
            self.assertIn(self.articles[0]['summary'], evidence['prompt'])
            self.assertEqual(record['content'], render_editorial(self.editorial, self.articles))

    def test_shortage_or_generation_failure_preserves_history_and_journals(self):
        for articles, responses in [([], []), (self.articles, [response('{}'), response('{}')])]:
            with tempfile.TemporaryDirectory() as root:
                history = Path(root)/'data/digests.json'
                history.parent.mkdir()
                history.write_text(json.dumps([self.record]))
                original = history.read_bytes()
                with self.assertRaises(RuntimeError):
                    self.run_main(root, articles, responses)
                self.assertEqual(history.read_bytes(), original)
                journal = json.loads(next((Path(root)/'.runs').glob('*.json')).read_text())
                self.assertEqual(journal['status'], 'failed')
                self.assertEqual(journal['materials'], articles)
                self.assertFalse((Path(root)/'data/runs').exists())


if __name__ == '__main__':
    unittest.main()
