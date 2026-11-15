import unittest
from unittest.mock import patch
import response_quality as rq


class ResponseQualityTests(unittest.TestCase):
    def test_screenshot_question(self):
        answer = rq.capability_answer('ты самостоятельно изучаешь интернет?', False)
        self.assertIn('не переобучаю', answer)
        self.assertIn('Brave Search API', answer)
        self.assertNotIn('learn from', answer)

    def test_web_enabled(self):
        self.assertIn('могу искать', rq.capability_answer('Ты обучаешься на данных сайтов?', True))

    def test_unrelated_not_intercepted(self):
        self.assertIsNone(rq.capability_answer('Как изучать Python самостоятельно?'))

    def test_mixed_language(self):
        self.assertTrue(rq.is_mixed_russian_answer('Расскажи о себе',
            'Я не_selfstudy, я обучен на базе данных и learn from sources.'))
        self.assertFalse(rq.is_mixed_russian_answer('Напиши пример на Python',
            'Для Python можно использовать API и JSON. Код: `print("hello world")`.'))
        self.assertFalse(rq.is_mixed_russian_answer('Explain this', 'I learn from sources.'))

    def test_core_stores_honest_answer_without_ollama(self):
        import core
        with patch.object(core, 'save_message') as save, \
                patch.object(core, 'history', return_value=[{'role': 'assistant', 'content': 'ok'}]), \
                patch.object(core, 'extract_explicit_memory', return_value=None), \
                patch.dict('os.environ', {}, clear=True):
            chat, cleared = core.answer('Ты самостоятельно изучаешь интернет?', 'tester')
        self.assertEqual(cleared, '')
        self.assertEqual(save.call_count, 2)
        self.assertIn('не переобучаю', save.call_args.args[2])


if __name__ == '__main__':
    unittest.main()
