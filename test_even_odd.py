
def check_even_odd(number: int) -> str:
    return "Even" if number % 2 == 0 else "Odd"


def test_even_number():
    assert check_even_odd(4) == "Even"


def test_odd_number():
    assert check_even_odd(7) == "Odd"


def test_zero():
    assert check_even_odd(0) == "Even"


def test_negative_number():
    assert check_even_odd(-3) == "Odd"
