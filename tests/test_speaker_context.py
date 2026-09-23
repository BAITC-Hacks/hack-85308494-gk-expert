import unittest
from core.speaker_context import resolve_names
from core.nlp_extractor import NLPExtractor


def line(voice, text, start, end, **fields):
    return dict(speaker_id=voice, text=text, start=start, end=end, **fields)


class ContextTests(unittest.TestCase):
    def test_explicit_handoff_names_reply_and_earlier_voice_occurrences(self):
        segments = [line('b', 'Добрый день.', 0, 2), line('a', 'Гульмира Сериковна, вам слово.', 3, 5),
                    line('b', 'Спасибо. По итогам месяца...', 5.4, 10)]
        annotated, profiles = resolve_names(segments)
        self.assertEqual(annotated[0]['speaker_name'], 'Гульмира Сериковна')
        self.assertEqual(annotated[2]['speaker_name'], 'Гульмира Сериковна')
        self.assertEqual(annotated[1]['speaker_name'], None)
        self.assertEqual(annotated[2]['name_status'], 'inferred')

    def test_self_introduction_beats_a_weak_handoff_guess(self):
        segments = [line('a', 'Анна, вам слово.', 0, 2), line('b', 'Меня зовут Мария.', 3, 5)]
        annotated, _ = resolve_names(segments)
        self.assertEqual(annotated[1]['speaker_name'], 'Мария')
        self.assertEqual(annotated[1]['name_status'], 'self_introduced')

    def test_thanks_names_previous_voice(self):
        annotated, _ = resolve_names([line('a', 'Доклад окончен.', 0, 4), line('b', 'Спасибо, Тимур Булатович.', 4.2, 7)])
        self.assertEqual(annotated[0]['speaker_name'], 'Тимур Булатович')
        self.assertIsNone(annotated[1]['speaker_name'])

    def test_stop_address_names_previous_voice_without_relabeling_interrupter(self):
        annotated, _ = resolve_names([line('a', 'Я хочу добавить.', 0, 3), line('b', 'Иван, не перебивайте.', 2.8, 5)])
        self.assertEqual(annotated[0]['speaker_name'], 'Иван')
        self.assertIsNone(annotated[1]['speaker_name'])

    def test_silence_or_change_alone_never_invents_name(self):
        annotated, profiles = resolve_names([line('a', 'У нас есть проблема.', 0, 3), line('b', 'Да.', 5, 6)])
        self.assertTrue(all(p['name'] is None for p in profiles))
        self.assertNotEqual(annotated[0]['speaker'], annotated[1]['speaker'])

    def test_same_voice_after_address_is_not_the_named_addressee(self):
        annotated, _ = resolve_names([line('a', 'Анна, вам слово.', 0, 2), line('a', 'Хотя сначала замечание.', 2.5, 6)])
        self.assertIsNone(annotated[1]['speaker_name'])

    def test_long_gap_and_overlap_do_not_establish_identity(self):
        for reply in [line('b', 'Да, слушаю.', 30, 33), line('b', 'Да, слушаю.', 1, 4, overlap=True)]:
            _, profiles = resolve_names([line('a', 'Анна, вам слово.', 0, 2), reply])
            self.assertTrue(all(p['name'] is None for p in profiles))

    def test_conflicting_context_is_exposed(self):
        segments = [line('a', 'Анна, вам слово.', 0, 2), line('b', 'Хорошо.', 3, 5),
                    line('a', 'Иван, вам слово.', 6, 8), line('b', 'Принято.', 9, 11)]
        _, profiles = resolve_names(segments)
        profile = next(p for p in profiles if p['id'] == 'b')
        self.assertEqual(profile['name_status'], 'conflict')
        self.assertIsNone(profile['name'])
        self.assertEqual(set(profile['candidates']), {'Анна', 'Иван'})

    def test_kazakh_handoff(self):
        annotated, _ = resolve_names([line('a', 'Әлия, сізге сөз.', 0, 2), line('b', 'Рахмет. Біздің жоспар...', 3, 6)])
        self.assertEqual(annotated[1]['speaker_name'], 'Әлия')

    def test_roster_normalizes_case_inflection(self):
        annotated, _ = resolve_names([line('a', 'Слово Анне.', 0, 2), line('b', 'Спасибо.', 3, 6)], ['Анна'])
        self.assertEqual(annotated[1]['speaker_name'], 'Анна')

    def test_manual_override_propagates_to_all_turns(self):
        annotated, profiles = resolve_names([line('a', 'Добрый день.', 0, 2), line('a', 'План готов.', 5, 7)], overrides={'a': 'Әлия'})
        self.assertTrue(all(s['speaker'] == 'Әлия' for s in annotated))
        self.assertEqual(profiles[0]['name_status'], 'confirmed')

    def test_first_person_assignment_retains_voice_id(self):
        annotated, profiles = resolve_names([line('a', 'Анна, вам слово.', 0, 2), line('b', 'Я подготовлю отчёт завтра.', 3, 7)])
        meeting = NLPExtractor().process_transcript({'segments': annotated, 'speakers': profiles, 'diarization': 'performed'})
        self.assertEqual(meeting['tasks'][0]['assignee_speaker_id'], 'b')
        self.assertEqual(meeting['tasks'][0]['assignee_name_status'], 'inferred')
        self.assertEqual(meeting['dialogue'][1]['speaker_id'], 'b')

    def test_word_thanks_without_name_is_not_name(self):
        annotated, _ = resolve_names([line('a', 'Спасибо. Коллеги, продолжим.', 0, 3), line('b', 'Хорошо.', 4, 6)])
        self.assertTrue(all(s['speaker_name'] is None for s in annotated))

    def test_asr_period_instead_of_vocative_comma(self):
        annotated, _ = resolve_names([line('a', 'Айнур Каировна. Вы курируете проект. Что предлагаете?', 0, 6), line('b', 'Предлагаю согласовать сроки.', 7, 11)])
        self.assertEqual(annotated[1]['speaker_name'], 'Айнур Каировна')

    def test_kazakh_self_introduction_suffix(self):
        annotated, _ = resolve_names([line('a', 'Мен Әлиямын.', 0, 3)])
        self.assertEqual(annotated[0]['speaker_name'], 'Әлия')
