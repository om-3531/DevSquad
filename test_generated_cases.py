
def check_even_odd(number: int) -> str:
    return "Even" if number % 2 == 0 else "Odd"


def test_normal_even():
    assert check_even_odd(4) == "Even"


def test_normal_odd():
    assert check_even_odd(7) == "Odd"


def test_edge_zero():
    assert check_even_odd(0) == "Even"
