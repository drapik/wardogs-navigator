"""Russian grammar helpers shared by the interface and voice prompts."""


def plural(n, one, few, many):
    """Pick the Russian noun form for an integer count: 1 метр, 2 метра, 5 метров."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def count(n, one, few, many):
    return f'{n} {plural(n, one, few, many)}'
