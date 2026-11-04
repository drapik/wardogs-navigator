"""Game x/y coordinates and the navigator's existing map-pixel frame."""
from functools import lru_cache
import json
import math
import re
import numpy as np

from .maps import map_info
from .model import asset_path

_NUMBER = r'[+-]?\d+(?:\.\d+)?'


def parse_coordinates(text):
    text = str(text).strip().translate(str.maketrans({'，': ',', '：': ':', '（': '(', '）': ')'}))
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1].strip()
    match = re.fullmatch(rf'x\s*[:=]?\s*({_NUMBER})\s*[,;\s]\s*y\s*[:=]?\s*({_NUMBER})', text, re.I)
    if match is None:
        match = re.fullmatch(rf'({_NUMBER})\s*[/,]\s*({_NUMBER})', text)
    if match is None:
        raise ValueError('Введите полные игровые координаты, например x87.18, y33.31')
    point = [float(value) for value in match.groups()]
    if not all(math.isfinite(value) for value in point):
        raise ValueError('Координаты должны быть конечными числами')
    return point


def format_coordinates(point):
    return f'x{point[0]:.2f}, y{point[1]:.2f}'


@lru_cache(maxsize=3)
def coordinate_system(map_id):
    map_info(map_id)
    data = json.loads(asset_path('game-coordinates.json').read_text(encoding='utf-8'))['maps'][map_id]
    return np.asarray(data['game_to_map'], dtype=float), data['bounds']


def game_to_map(map_id, point):
    matrix, bounds = coordinate_system(map_id)
    x, y = point
    if not all(math.isfinite(value) for value in (x, y)):
        raise ValueError('Координаты должны быть конечными числами')
    if not (bounds['minX'] <= x <= bounds['maxX'] and bounds['minY'] <= y <= bounds['maxY']):
        raise ValueError('Игровые координаты за пределами текущей карты; проверьте выбор карты и значения')
    result = (matrix @ [x, y, 1]).tolist()
    info = map_info(map_id)
    if not (0 <= result[0] <= info['width'] and 0 <= result[1] <= info['height']):
        raise ValueError('Эти координаты за краем навигационной подложки')
    return result


def map_to_game(map_id, point):
    matrix, _ = coordinate_system(map_id)
    return np.linalg.solve(matrix[:, :2], np.asarray(point) - matrix[:, 2]).tolist()
