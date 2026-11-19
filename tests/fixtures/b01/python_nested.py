def outer(value: int) -> int:
    offset = value + 1

    def inner(multiplier: int) -> int:
        return offset * multiplier

    return inner(2)


def very_large_function() -> int:
    first = 1
    second = first + 2
    third = second + 3
    fourth = third + 4
    fifth = fourth + 5
    sixth = fifth + 6
    seventh = sixth + 7
    eighth = seventh + 8
    ninth = eighth + 9
    tenth = ninth + 10
    return tenth
