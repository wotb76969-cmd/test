import unittest
from unittest.mock import patch
import live_tools

class LiveTests(unittest.TestCase):
    def test_intent(self):
        self.assertEqual(live_tools.search_intent('Какая погода в Москве?'),'weather')
        self.assertEqual(live_tools.search_intent('Последние новости Python'),'web')
        self.assertIsNone(live_tools.search_intent('Объясни переменные Python'))
    def test_greeting(self):
        self.assertIn('Привет',live_tools.chat_greeting('Привет!'))
        self.assertIn('порядке',live_tools.chat_greeting('Как дела?'))
    def test_weather_without_location(self):
        result, sources = live_tools.weather('Какая погода?')
        self.assertIn('город',result)
        self.assertEqual(sources,[])
    def test_weather_with_mocked_data(self):
        geo={'results':[{'name':'Москва','country':'Россия','latitude':55.75,'longitude':37.62}]}
        forecast={'current':{'temperature_2m':6,'apparent_temperature':3,'relative_humidity_2m':65,'wind_speed_10m':8,'weather_code':2,'time':'2026-11-11T12:00'}}
        with patch.object(live_tools,'fetch_json',side_effect=[geo,forecast]) as fetch:
            answer, sources = live_tools.weather('Какая погода в Москве сегодня?')
        self.assertIn('6 °C',answer)
        self.assertEqual(len(sources),1)
        self.assertEqual(fetch.call_count,2)
    def test_no_key_message(self):
        with patch.dict('os.environ',{},clear=True):
            results, note=live_tools.brave_search('новости')
        self.assertEqual(results,[])
        self.assertIn('CHELSY_BRAVE_SEARCH_KEY',note)
    def test_brave_result_mocked(self):
        with patch.dict('os.environ',{'CHELSY_BRAVE_SEARCH_KEY':'unit-test'}):
            with patch.object(live_tools,'fetch_json',return_value={'web':{'results':[{'url':'https://example.org/a','title':'Test','description':'Current info'}]}}):
                results,note=live_tools.brave_search('news')
        self.assertEqual(results[0]['title'],'Test')
        self.assertEqual(note,'')
if __name__=='__main__': unittest.main()
