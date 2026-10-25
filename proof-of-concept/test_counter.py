"""Exercise changes in digit count and all segment patterns around a million."""

import unittest

from PIL import Image, ImageDraw

from read_counter import DIGITS, recognize


class CounterRangeTests(unittest.TestCase):
    def test_variable_length_readings(self):
        segments = [
            (10, 0, 40, 8), (42, 10, 50, 34), (42, 42, 50, 66),
            (10, 68, 40, 76), (0, 42, 8, 66), (0, 10, 8, 34),
            (10, 34, 40, 42),
        ]
        patterns = {value: pattern for pattern, value in DIGITS.items()}
        for text in ("58857", "999999", "1000000", "1000001", "1234567",
                     "1999999", "10000000"):
            with self.subTest(text=text):
                image = Image.new("RGB", (1920, 1080), (20, 20, 20))
                draw = ImageDraw.Draw(image)
                for index, digit in enumerate(text):
                    for enabled, box in zip(patterns[digit], segments):
                        if enabled == "1":
                            x0, y0, x1, y1 = box
                            draw.rectangle((200+index*75+x0, 370+y0,
                                            200+index*75+x1, 370+y1),
                                           fill=(255, 60, 60))
                result, _ = recognize(image)
                self.assertEqual(result["text"], text)
                self.assertEqual(result["value"], int(text))


if __name__ == "__main__":
    unittest.main()
