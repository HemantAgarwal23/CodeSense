"""Labeled benchmark snippets.

Each buggy case contains exactly one known bug. The buggy line ends with
BUG_MARKER; the runner strips the marker before review, so the reviewer never
sees it. `keywords` are used to match findings that carry no line number.
Clean cases have no marker and no keywords; any finding on them is a false alarm.
"""

BUG_MARKER = "#@bug"

CASES = [
    {
        "id": "division-by-zero-literal",
        "keywords": ["zero", "division", "zerodivision"],
        "code": """
def average(values):
    total = sum(values)
    return total / 0  #@bug
""",
    },
    {
        "id": "division-by-empty-length",
        "keywords": ["empty", "zero", "division", "zerodivision"],
        "code": """
def mean(values):
    return sum(values) / len(values)  #@bug
""",
    },
    {
        "id": "missing-key-on-empty-dict",
        "keywords": ["keyerror", "key error", "missing key", "empty dict"],
        "code": """
def lookup(user_id):
    users = {}
    return users[user_id]  #@bug
""",
    },
    {
        "id": "index-past-end",
        "keywords": ["indexerror", "out of range", "off-by-one", "off by one", "index"],
        "code": """
def last_item(items):
    return items[len(items)]  #@bug
""",
    },
    {
        "id": "loop-reads-past-end",
        "keywords": ["indexerror", "out of range", "off-by-one", "off by one", "i + 1"],
        "code": """
def sum_pairs(values):
    total = 0
    for i in range(len(values)):
        total += values[i] + values[i + 1]  #@bug
    return total
""",
    },
    {
        "id": "regex-match-may-be-none",
        "keywords": ["none", "attributeerror", "no match"],
        "code": """
import re


def extract_year(text):
    match = re.search("[0-9]{4}", text)
    return match.group(0)  #@bug
""",
    },
    {
        "id": "sql-injection",
        "keywords": ["sql", "injection"],
        "code": """
def find_user(cursor, username):
    query = f"SELECT * FROM users WHERE name = '{username}'"  #@bug
    cursor.execute(query)
    return cursor.fetchone()
""",
    },
    {
        "id": "subprocess-shell-injection",
        "keywords": ["shell", "command injection", "injection"],
        "code": """
import subprocess


def list_dir(path):
    return subprocess.run(f"ls {path}", shell=True, capture_output=True)  #@bug
""",
    },
    {
        "id": "eval-on-input",
        "keywords": ["eval", "code execution", "arbitrary code", "injection"],
        "code": """
def calculate(expression):
    return eval(expression)  #@bug
""",
    },
    {
        "id": "hardcoded-api-key",
        "keywords": ["hardcoded", "hard-coded", "secret", "api key", "credential"],
        "code": """
import requests

API_KEY = "codesense-fake-key-4f9a2b7c1e8d"  #@bug


def charge(amount):
    return requests.post("https://api.example.com/charge", json={"amount": amount, "key": API_KEY})
""",
    },
    {
        "id": "pickle-untrusted-data",
        "keywords": ["pickle", "deserializ", "untrusted"],
        "code": """
import pickle


def load_session(raw_bytes):
    return pickle.loads(raw_bytes)  #@bug
""",
    },
    {
        "id": "file-never-closed",
        "keywords": ["close", "leak", "context manager", "with open", "with statement"],
        "code": """
def read_config(path):
    handle = open(path)  #@bug
    return handle.read()
""",
    },
    {
        "id": "mutable-default-argument",
        "keywords": ["mutable default", "default argument", "default value", "shared between calls"],
        "code": """
def add_tag(tag, tags=[]):  #@bug
    tags.append(tag)
    return tags
""",
    },
    {
        "id": "exception-swallowed",
        "keywords": ["swallow", "silently", "broad except", "bare except", "except exception"],
        "code": """
import json


def parse(payload):
    try:
        return json.loads(payload)
    except Exception:  #@bug
        pass
""",
    },
    {
        "id": "recursion-without-base-case",
        "keywords": ["recursion", "base case", "recursionerror", "infinite"],
        "code": """
def factorial(n):
    return n * factorial(n - 1)  #@bug
""",
    },
    {
        "id": "late-binding-lambda",
        "keywords": ["late binding", "late-binding", "closure", "same value", "captures"],
        "code": """
def make_multipliers():
    return [lambda x: x * i for i in range(5)]  #@bug
""",
    },
    {
        "id": "computed-value-not-returned",
        "keywords": ["return", "returns none", "no return"],
        "code": """
def apply_discount(price, rate):
    discounted = price * (1 - rate)  #@bug
""",
    },
    {
        "id": "identity-compare-with-literal",
        "keywords": ["identity", "equality", "use ==", "is operator", "'is'", "\"is\""],
        "code": """
def is_admin(role):
    return role is "admin"  #@bug
""",
    },
    {
        "id": "range-check-always-true",
        "keywords": ["always true", "always evaluate", "should be and", "use and", "tautolog"],
        "code": """
def is_valid_percentage(value):
    return value >= 0 or value <= 100  #@bug
""",
    },
    {
        "id": "unreachable-code",
        "keywords": ["unreachable", "dead code", "never executed", "never be executed"],
        "code": """
def status(code):
    return "ok"
    if code != 200:  #@bug
        return "error"
""",
    },
    {
        "id": "list-mutated-while-iterating",
        "keywords": ["while iterating", "during iteration", "modifying the list", "skip"],
        "code": """
def remove_negatives(values):
    for value in values:
        if value < 0:
            values.remove(value)  #@bug
    return values
""",
    },
    {
        "id": "variable-maybe-unassigned",
        "keywords": ["unbound", "before assignment", "not defined", "undefined", "may not be assigned"],
        "code": """
def classify(score):
    if score > 90:
        grade = "A"
    elif score > 75:
        grade = "B"
    return grade  #@bug
""",
    },
    {
        "id": "string-plus-int",
        "keywords": ["typeerror", "concatenat", "str(", "string and int", "convert"],
        "code": """
def greeting(name, age):
    return "Hello " + name + ", you are " + age  #@bug
""",
    },
    {
        "id": "float-equality",
        "keywords": ["float", "precision", "isclose", "rounding"],
        "code": """
def is_total_correct(a, b):
    return a + b == 0.3  #@bug
""",
    },
    {
        "id": "insecure-random-token",
        "keywords": ["secrets", "cryptographic", "predictable", "insecure random", "not secure"],
        "code": """
import random
import string


def reset_token():
    return "".join(random.choice(string.ascii_letters) for _ in range(32))  #@bug
""",
    },
    {
        "id": "path-traversal",
        "keywords": ["traversal", "sanitize", "outside the upload", "directory"],
        "code": """
from pathlib import Path

UPLOAD_DIR = Path("/srv/uploads")


def read_upload(filename):
    return (UPLOAD_DIR / filename).read_text()  #@bug
""",
    },
    {
        "id": "shared-class-attribute",
        "keywords": ["class attribute", "class variable", "shared", "all instances"],
        "code": """
class Cart:
    items = []  #@bug

    def add(self, item):
        self.items.append(item)
""",
    },
    {
        "id": "counter-overwritten",
        "keywords": ["count += 1", "increment", "always 1", "overwrit", "reset"],
        "code": """
def count_evens(values):
    count = 0
    for value in values:
        if value % 2 == 0:
            count = 1  #@bug
    return count
""",
    },
    {
        "id": "default-evaluated-once",
        "keywords": ["evaluated once", "definition time", "same timestamp", "default argument", "default value"],
        "code": """
from datetime import datetime


def log_event(message, timestamp=datetime.now()):  #@bug
    return f"{timestamp.isoformat()} {message}"
""",
    },
    {
        "id": "sort-returns-none",
        "keywords": ["returns none", "sorted(", "in place", "in-place"],
        "code": """
def sorted_names(names):
    return names.sort()  #@bug
""",
    },
    {
        "id": "clean-typed-add",
        "keywords": [],
        "code": """
def add(a: int, b: int) -> int:
    return a + b
""",
    },
    {
        "id": "clean-guarded-mean",
        "keywords": [],
        "code": """
def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("values must not be empty")
    return sum(values) / len(values)
""",
    },
    {
        "id": "clean-context-manager",
        "keywords": [],
        "code": """
from pathlib import Path


def read_config(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        return handle.read()
""",
    },
    {
        "id": "clean-parameterized-sql",
        "keywords": [],
        "code": """
def find_user(cursor, username: str):
    cursor.execute("SELECT * FROM users WHERE name = %s", (username,))
    return cursor.fetchone()
""",
    },
    {
        "id": "clean-factorial",
        "keywords": [],
        "code": """
def factorial(n: int) -> int:
    if n < 0:
        raise ValueError("n must be non-negative")
    if n <= 1:
        return 1
    return n * factorial(n - 1)
""",
    },
    {
        "id": "clean-none-default",
        "keywords": [],
        "code": """
def add_tag(tag: str, tags: list[str] | None = None) -> list[str]:
    tags = [] if tags is None else tags
    tags.append(tag)
    return tags
""",
    },
    {
        "id": "clean-secure-token",
        "keywords": [],
        "code": """
import secrets


def reset_token() -> str:
    return secrets.token_urlsafe(32)
""",
    },
    {
        "id": "clean-dict-get",
        "keywords": [],
        "code": """
def lookup(users: dict[str, str], user_id: str) -> str | None:
    return users.get(user_id)
""",
    },
    {
        "id": "clean-guarded-regex",
        "keywords": [],
        "code": """
import re


def extract_year(text: str) -> str | None:
    match = re.search("[0-9]{4}", text)
    return match.group(0) if match else None
""",
    },
    {
        "id": "clean-filter-comprehension",
        "keywords": [],
        "code": """
def remove_negatives(values: list[int]) -> list[int]:
    return [value for value in values if value >= 0]
""",
    },
]
