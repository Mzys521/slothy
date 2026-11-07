"""Build V3 profile edits for pre-existing behavior regression cases.
/ 用 V3 属性契约表达既有业务回归场景。"""


def attributes(values):
    return [
        {"key": key, "value": value, "dimension": "preference", "scope": "global"}
        for key, value in values.items()
    ]
