import re

class VolumeUtil:
    _CHINESE_NUMBER_MAP = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8,
        '九': 9, '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
        '十六': 16, '十七': 17, '十八': 18, '十九': 19, '二十': 20, '二十一': 21,
        '二十二': 22, '二十三': 23, '二十四': 24, '二十五': 25, '二十六': 26,
        '二十七': 27, '二十八': 28, '二十九': 29, '三十': 30,
    }
    
    @classmethod
    def get_series_index(cls, volume_name):
        return cls._get_by_last_num(volume_name) or cls._get_by_volume_name(volume_name)
    
    @classmethod
    def _get_by_last_num(cls, volume_name):
        m = re.search(r'\s(\d+(?:\.\d)?)$', volume_name)
        if m:
            return float(m.group(1))
        return None
    
    @classmethod
    def _get_by_volume_name(cls, volume_name):
        m = re.search(r'第([一二三四五六七八九十]+)[卷话章]$', volume_name)
        if m:
            return cls._CHINESE_NUMBER_MAP.get(m.group(1))
        return None
