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
from newsgen import evaluate, feeds, llm, prompts, settings, storage
from digest_schema import validate_editorial, render_editorial

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/digest-v2.json').read_text())


def response(content, reason='stop', usage=None):
    return SimpleNamespace(id='mock-response', model='mock-model', usage=SimpleNamespace(model_dump=lambda: usage or {'total_tokens': 42}),
                           choices=[SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content))])


def aliased(section, articles):
    """What the model sees and returns: per-section aliases (A1...) instead of material IDs."""
    candidates = [a['id'] for a in articles if a['category'] == section['category']]
    alias = {real: f'A{number}' for number, real in enumerate(candidates, 1)}
    return {**section, 'items': [{**item, 'source_ids': [alias[ref] for ref in item['source_ids']]}
                                 for item in section['items']]}


def editorial_responses(editorial, articles=FIXTURE['materials']):
    return [response(json.dumps(aliased(section, articles), ensure_ascii=False)) for section in editorial['sections']] + [
        response(json.dumps({'overview': editorial['overview']}))]


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.record = copy.deepcopy(FIXTURE)
        self.articles = self.record['materials']
        self.editorial = self.record['editorial']
        self.minimum = patch.object(settings, 'MIN_ITEMS_PER_CATEGORY', 3)
        self.minimum.start()
        self.addCleanup(self.minimum.stop)

    def mocked_client(self, values):
        create = Mock(side_effect=values)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return create, patch.object(llm, 'OpenAI', return_value=client)

    def test_invalid_first_response_retries_with_feedback(self):
        create, mock = self.mocked_client([response('{}')] + editorial_responses(self.editorial))
        run = {}
        with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            result = generate.generate_digest(self.articles, run)
        self.assertEqual(result, self.editorial)
        self.assertEqual(create.call_count, 8)
        self.assertIn('上次输出未通过校验', create.call_args_list[1].kwargs['messages'][1]['content'])
        self.assertEqual([a['status'] for a in run['attempts']], ['invalid'] + ['valid'] * 7)
        self.assertEqual(run['attempts'][1]['usage']['total_tokens'], 42)

    def test_empty_malformed_and_truncated_responses_fail_boundedly(self):
        for value in [response(''), response('not json'), response(json.dumps(self.editorial), 'length')]:
            with self.subTest(value=value):
                create, mock = self.mocked_client([value, value])
                with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
                    with self.assertRaisesRegex(RuntimeError, '2 次输出未通过校验'):
                        generate.generate_digest(self.articles)
                self.assertEqual(create.call_count, 2)

    def test_truncated_section_retries_only_that_section_with_more_budget(self):
        values = editorial_responses(self.editorial)
        values.insert(1, response('{"category":', 'length', {'completion_tokens': 6000,
            'completion_tokens_details': {'reasoning_tokens': 5900}}))
        create, mock = self.mocked_client(values)
        run = {}
        with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            result = generate.generate_digest(self.articles, run)
        self.assertEqual(result, self.editorial)
        self.assertEqual(create.call_count, 8)
        self.assertEqual([len(r['attempts']) for r in run['requests']], [1, 2, 1, 1, 1, 1, 1])
        retry = create.call_args_list[2].kwargs
        self.assertEqual(retry['max_tokens'], 12000)
        self.assertEqual(create.call_args_list[1].kwargs['max_tokens'], 6000)
        self.assertIn('最多 3 个独立事件', retry['messages'][1]['content'])
        self.assertIn('最多 100 字符', retry['messages'][1]['content'])
        self.assertEqual(retry['extra_body']['reasoning']['effort'], 'low')
        self.assertEqual(run['requests'][1]['attempts'][0]['usage']['completion_tokens_details']['reasoning_tokens'], 5900)
        # Each section receives only its own candidate material, and overview only accepted events.
        self.assertNotIn(self.articles[3]['id'], create.call_args_list[0].kwargs['messages'][1]['content'])
        self.assertIn(self.editorial['sections'][0]['items'][0]['title'], create.call_args_list[-1].kwargs['messages'][1]['content'])
        self.assertNotIn(self.articles[0]['feed_url'], create.call_args_list[-1].kwargs['messages'][1]['content'])

    def test_model_sees_short_aliases_and_slim_materials_but_records_real_ids(self):
        create, mock = self.mocked_client(editorial_responses(self.editorial))
        with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            result = generate.generate_digest(self.articles)
        self.assertEqual(result, self.editorial)
        first = create.call_args_list[0].kwargs
        prompt = first['messages'][1]['content']
        self.assertIn('"id":"A1"', prompt)
        for article in self.articles:
            self.assertNotIn(article['id'], prompt)
            self.assertNotIn(article['feed_url'], prompt)
            self.assertNotIn(article['fetched_at'], prompt)
        self.assertEqual(first['response_format'], {'type': 'json_object'})
        self.assertEqual(first['temperature'], settings.CONFIG['temperature'])
        # Stable editorial rules lead the prompt so the cacheable prefix is identical across sections.
        sent = [call.kwargs['messages'][1]['content'] for call in create.call_args_list[:6]]
        self.assertEqual(len({p[:len(prompts.EDITORIAL_RULES)] for p in sent}), 1)

    def test_unknown_alias_is_rejected_like_a_forged_id(self):
        bad = aliased(self.editorial['sections'][0], self.articles)
        bad['items'][0]['source_ids'] = ['A99']
        values = editorial_responses(self.editorial)
        values.insert(0, response(json.dumps(bad, ensure_ascii=False)))
        run = {}
        create, mock = self.mocked_client(values)
        with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            generate.generate_digest(self.articles, run)
        self.assertEqual([len(r['attempts']) for r in run['requests']], [2, 1, 1, 1, 1, 1, 1])
        self.assertIn('素材 ID 不存在', run['requests'][0]['attempts'][0]['error'])

    def test_existing_issue_is_skipped_unless_forced(self):
        with tempfile.TemporaryDirectory() as root:
            history = Path(root)/'data/digests/2026-10-12.json'
            history.parent.mkdir(parents=True)
            history.write_text(json.dumps({**self.record, 'date': '2026-10-12'}))
            before = history.read_bytes()
            create = self.run_main(root, self.articles, [])
            self.assertEqual(create.call_count, 0)
            self.assertEqual(history.read_bytes(), before)
            self.assertFalse((Path(root)/'.runs').exists())
            create = self.run_main(root, self.articles, editorial_responses(self.editorial), argv=['--force'])
            self.assertEqual(create.call_count, 7)
            self.assertEqual(json.loads(history.read_text())['schema_version'], 2)

    def test_link_is_unique_across_categories_but_titles_only_within_one(self):
        start = datetime(2026, 10, 11, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        entry = {'title': '同一报道', 'link': 'https://example.org/same', 'published_parsed': start.utctimetuple()}
        configs = [{'category': settings.CATEGORIES[2], 'name': 'ai', 'url': 'https://example.org/ai'},
                   {'category': settings.CATEGORIES[4], 'name': 'tech', 'url': 'https://example.org/tech'}]
        with patch.object(feeds, 'load_feeds', return_value=configs), \
             patch.object(feeds, 'fetch_feed', return_value=SimpleNamespace(entries=[entry], error=None)):
            articles, _ = feeds.collect_articles(start, end)
        self.assertEqual([a['category'] for a in articles], [settings.CATEGORIES[2]])

    def test_overview_truncation_does_not_regenerate_sections(self):
        values = editorial_responses(self.editorial)
        values.insert(6, response('', 'length'))
        create, mock = self.mocked_client(values)
        run = {}
        with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            result = generate.generate_digest(self.articles, run)
        self.assertEqual(result, self.editorial)
        self.assertEqual([len(r['attempts']) for r in run['requests']], [1] * 6 + [2])
        self.assertEqual(create.call_args.kwargs['max_tokens'], 3000)
        self.assertIn('最多 100 字符', create.call_args.kwargs['messages'][1]['content'])

    def test_retry_budget_is_capped_and_reasoning_can_be_omitted(self):
        config = {**settings.CONFIG, 'max_output_tokens': 7000, 'reasoning_effort': None}
        values = [response('', 'length')] + editorial_responses(self.editorial)
        create, mock = self.mocked_client(values)
        with mock, patch.object(settings, 'CONFIG', config), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            generate.generate_digest(self.articles)
        self.assertEqual(create.call_args_list[1].kwargs['max_tokens'], 7000)
        self.assertTrue(all('extra_body' not in call.kwargs for call in create.call_args_list))

    def test_section_retry_rejects_shortages_oversized_output_and_cross_section_duplicates(self):
        for mutate in [lambda section: section['items'].pop(),
                       lambda section: section['items'][0].update(summary='长' * 161),
                       lambda section: section['items'][0].update(title=self.editorial['sections'][0]['items'][0]['title'])]:
            invalid = copy.deepcopy(self.editorial['sections'][1])
            mutate(invalid)
            values = editorial_responses(self.editorial)
            values.insert(1, response(json.dumps(invalid)))
            create, mock = self.mocked_client(values)
            run = {}
            with mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
                result = generate.generate_digest(self.articles, run)
            self.assertEqual(result, self.editorial)
            self.assertEqual([len(r['attempts']) for r in run['requests']], [1, 2, 1, 1, 1, 1, 1])

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

    def test_title_normalization_matches_shared_vectors(self):
        from digest_schema import normalized_title
        for raw, expected in json.loads((Path(__file__).parent / 'fixtures/normalize-cases.json').read_text(encoding='utf-8')):
            self.assertEqual(normalized_title(raw), expected, raw)

    def test_categories_and_limits_come_from_shared_config(self):
        import digest_schema
        shared = json.loads((Path(__file__).parents[1] / 'config/news.json').read_text(encoding='utf-8'))
        self.assertEqual(digest_schema.CATEGORIES, tuple(c['title'] for c in shared['categories']))
        self.assertEqual(digest_schema.SUMMARY_MAX, shared['summary_max_chars'])

    def test_evaluation_reports_counters_for_success_and_failure(self):
        def fake(materials, run):
            run['attempts'] = [{'attempt': 1, 'status': 'invalid', 'finish_reason': 'length', 'usage': {'completion_tokens': 5, 'prompt_tokens': 7}},
                               {'attempt': 2, 'status': 'valid', 'finish_reason': 'stop', 'usage': {'completion_tokens': 3}}]
            return self.editorial
        row = evaluate.evaluate(self.articles, 'x', generate=fake)
        self.assertTrue(row['ok'])
        self.assertEqual((row['requests'], row['retries'], row['invalid'], row['truncated']), (2, 1, 1, 1))
        self.assertEqual((row['completion_tokens'], row['prompt_tokens']), (8, 7))
        self.assertEqual(sum(row['items'].values()), len(row['titles']))
        def boom(materials, run):
            raise RuntimeError('bad')
        failed = evaluate.evaluate(self.articles, 'x', generate=boom)
        self.assertFalse(failed['ok'])
        self.assertIn('RuntimeError', failed['error'])

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
        self.assertEqual(early[2] - early[1], timedelta(hours=settings.LOOKBACK_HOURS))
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
        config = {'category': settings.CATEGORIES[0], 'name': 'source', 'url': 'https://example.org/rss'}
        with patch.object(feeds, 'load_feeds', return_value=[config]), patch.object(feeds, 'fetch_feed', return_value=SimpleNamespace(entries=entries, error=None)):
            first, _ = feeds.collect_articles(start, end)
            second, _ = feeds.collect_articles(start, end)
        self.assertEqual({a['title'] for a in first}, {'1', '2'})
        self.assertEqual([a['id'] for a in first], [a['id'] for a in second])

    def test_backfill_writes_only_its_own_issue_file(self):
        with tempfile.TemporaryDirectory() as root:
            digests = Path(root)/'data/digests'
            digests.mkdir(parents=True)
            latest = digests/'2026-10-12.json'
            latest.write_text('latest')
            with patch.object(storage, 'DIGEST_DIR', digests):
                storage.save_record({'date': '2026-01-01', 'title': 'old'})
                storage.save_record({'date': '2026-01-01', 'title': 'replaced'})
                self.assertTrue(storage.issue_exists('2026-01-01'))
                self.assertFalse(storage.issue_exists('2026-01-02'))
            self.assertEqual(latest.read_text(), 'latest')
            self.assertEqual(json.loads((digests/'2026-01-01.json').read_text())['title'], 'replaced')
            self.assertEqual(sorted(p.name for p in digests.iterdir()), ['2026-01-01.json', '2026-10-12.json'])

    def test_atomic_write_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'history.json'
            path.write_text('original')
            with patch.object(storage.os, 'replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    storage.atomic_json(path, [])
            self.assertEqual(path.read_text(), 'original')
            self.assertEqual(len(list(Path(root).iterdir())), 1)

    def run_main(self, root, articles, api_values, argv=()):
        path = Path(root)
        create, mock = self.mocked_client(api_values)
        with patch.multiple(generate, ROOT=path, DATA_DIR=path/'data', RUN_DIR=path/'.runs'), \
             patch.object(storage, 'DIGEST_DIR', path/'data/digests'), \
             patch.object(generate, 'collect_articles', return_value=(articles, [])), \
             patch.object(generate, 'issue_window', return_value=('2026-10-12', datetime(2026,10,11,0,17,tzinfo=timezone.utc), datetime(2026,10,12,0,17,tzinfo=timezone.utc))), \
             mock, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock'}):
            generate.main(list(argv))
        return create

    def test_main_persists_evidence_and_structured_record(self):
        with tempfile.TemporaryDirectory() as root:
            self.run_main(root, self.articles, editorial_responses(self.editorial))
            record = json.loads((Path(root)/'data/digests/2026-10-12.json').read_text())
            evidence = json.loads((Path(root)/record['generation']['evidence_path']).read_text())
            self.assertEqual(record['schema_version'], 2)
            self.assertEqual(record['generation']['strategy'], 'sections-v1')
            self.assertEqual(len(evidence['requests']), 7)
            self.assertEqual(evidence['editorial'], self.editorial)
            self.assertEqual(record['materials'], self.articles)
            self.assertEqual(evidence['prompt_version'], generate.PROMPT_VERSION)
            self.assertEqual(evidence['attempts'][0]['raw_response'], json.dumps(aliased(self.editorial['sections'][0], self.articles), ensure_ascii=False))
            self.assertEqual(evidence['attempts'][0]['source_aliases']["A1"], self.editorial['sections'][0]['items'][0]['source_ids'][0])
            self.assertIn(self.articles[0]['summary'], evidence['prompt'])
            self.assertEqual(record['content'], render_editorial(self.editorial, self.articles))

    def test_shortage_or_generation_failure_preserves_history_and_journals(self):
        for articles, responses in [([], []), (self.articles, [response('{}'), response('{}')]),
                                    (self.articles, [editorial_responses(self.editorial)[0], response('', 'length'), response('', 'length')])]:
            with tempfile.TemporaryDirectory() as root:
                history = Path(root)/'data/digests/2026-10-11.json'
                history.parent.mkdir(parents=True)
                history.write_text(json.dumps({**self.record, 'date': '2026-10-11'}))
                original = history.read_bytes()
                with self.assertRaises(RuntimeError):
                    self.run_main(root, articles, responses)
                self.assertEqual(history.read_bytes(), original)
                journal = json.loads(next((Path(root)/'.runs').glob('*.json')).read_text())
                self.assertEqual(journal['status'], 'failed')
                self.assertEqual(journal['materials'], articles)
                self.assertFalse((Path(root)/'data/runs').exists())
                if len(responses) == 3:
                    self.assertEqual(journal['accepted_sections'], self.editorial['sections'][:1])


if __name__ == '__main__':
    unittest.main()
