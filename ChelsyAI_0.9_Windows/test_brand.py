import unittest
import app
from PIL import Image

class TestBrand(unittest.TestCase):
    def test_logo_exists(self):
        self.assertTrue(app.asset_path('chelsy_logo_small.png').is_file())
        with Image.open(app.asset_path('chelsy_logo_small.png')) as img:
            self.assertEqual(img.size, (256,256))
    def test_brand_in_html(self):
        header = app.brand_header_html()
        self.assertIn('Chelsy AI', header)
        self.assertIn('data:image/png;base64,', header)

if __name__ == '__main__': unittest.main()
