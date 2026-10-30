import unittest
import re
from urllib.parse import unquote
import requests


def dms_to_dd(deg, min, sec, dir):
    dd = float(deg) + float(min) / 60.0 + float(sec) / 3600.0
    if dir.upper() in ["S", "W"]:
        dd = -dd
    return dd


def extract_coords(text):
    text = unquote(text or "").strip()

    # 1. Degree Minute Second: 12°44'36.5"N 77°49'02.8"E or similar
    dms_matches = re.findall(
        r"(\d{1,2})[°\s]+(\d{1,2})['′\s]+([\d.]+)[\"″\s]*([NSEWnsew])", text
    )
    if len(dms_matches) == 2:
        lat = dms_to_dd(*dms_matches[0])
        lng = dms_to_dd(*dms_matches[1])
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return round(lat, 6), round(lng, 6)

    # 2. Signed decimal with cardinal direction: 12.743475N, 77.817446E
    m_card = re.search(
        r"(-?\d{1,2}\.\d+)\s*[°]?\s*([NSns])\s*[,;\s]+\s*(-?\d{1,3}\.\d+)\s*[°]?\s*([EWew])",
        text,
    )
    if m_card:
        lat = float(m_card.group(1)) * (-1 if m_card.group(2).upper() == "S" else 1)
        lng = float(m_card.group(3)) * (-1 if m_card.group(4).upper() == "W" else 1)
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return round(lat, 6), round(lng, 6)

    # 3. Standard URL and coordinate patterns
    patterns = [
        r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)",
        r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)",
        r"[?&](?:q|ll|query|center|destination|daddr|saddr)=(?:loc:)?(-?\d{1,2}\.\d+)[,\s+](-?\d{1,3}\.\d+)",
        r"/(?:place|search)/(-?\d{1,2}\.\d+)[,\s+](-?\d{1,3}\.\d+)",
        r"geo:(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)",
        r"(-?\d{1,2}\.\d{3,})\s*[,;\s]\s*(-?\d{1,3}\.\d{3,})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return round(lat, 6), round(lng, 6)
    return None


class CoordinateParserTest(unittest.TestCase):
    def test_all_formats(self):
        cases = {
            "12.743475, 77.817446": (12.743475, 77.817446),
            "12.743475,77.817446": (12.743475, 77.817446),
            "12.743475 77.817446": (12.743475, 77.817446),
            "(12.743475, 77.817446)": (12.743475, 77.817446),
            "Location: 12.743475, 77.817446 (Factory)": (12.743475, 77.817446),
            "https://www.google.com/maps/@12.743475,77.817446,17z": (12.743475, 77.817446),
            "https://www.google.com/maps/place/12.743475,77.817446": (12.743475, 77.817446),
            "https://www.google.com/maps/place/12.743475+77.817446": (12.743475, 77.817446),
            "https://www.google.com/maps/search/12.743475,77.817446": (12.743475, 77.817446),
            "https://maps.google.com/?q=loc:12.743475+77.817446": (12.743475, 77.817446),
            "https://maps.google.com/?daddr=12.743475,77.817446": (12.743475, 77.817446),
            "https://maps.apple.com/?ll=12.743475,77.817446": (12.743475, 77.817446),
            "geo:12.743475,77.817446?z=17": (12.743475, 77.817446),
            "12.743475N, 77.817446E": (12.743475, 77.817446),
            "12.743475 N, 77.817446 E": (12.743475, 77.817446),
            '12°44\'36.5"N 77°49\'02.8"E': (12.743472, 77.817444),
        }
        for inp, expected in cases.items():
            res = extract_coords(inp)
            self.assertIsNotNone(res, f"Failed on: {inp}")
            self.assertAlmostEqual(res[0], expected[0], places=4, msg=f"Lat mismatch for {inp}")
            self.assertAlmostEqual(res[1], expected[1], places=4, msg=f"Lng mismatch for {inp}")


if __name__ == "__main__":
    unittest.main()
